from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import AsyncGenerator

from app.services.facebook import FacebookService
from app.repositories import (
    upsert_page_conversation, 
    bulk_upsert_conversation_messages,
    get_latest_message_time,
    get_conversation_updated_time,
    check_message_exists
)

from app.task import create_task_if_not_running, update_task, heartbeat_task, get_task, STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELED, TYPE_SYNC

logger = logging.getLogger("uvicorn.error")

class ChatSyncService:
    def __init__(self, fb_service: FacebookService):
        self.fb = fb_service
        self.semaphore = asyncio.Semaphore(10)  # Max 10 concurrent conversation fetches
        self.messages_synced = 0
        self.conversations_synced = 0
        self.task_key = "chat_sync"  # Overridden in sync_all_chats with page-specific key

    def _iso_to_unix(self, iso_str: str) -> int:
        """Convert ISO 8601 string to Unix timestamp."""
        try:
            # Handle formats like 2023-10-27T10:00:00+0000
            dt = datetime.fromisoformat(iso_str.replace("+0000", "+00:00"))
            return int(dt.timestamp())
        except Exception:
            return 0

    async def start_sync(self, page_id: str, full_sync: bool = False) -> dict:
        """Start a background sync worker. Returns immediately with status dict."""
        self.task_key = f"chat_sync_{page_id}"
        created = await create_task_if_not_running(self.task_key, "聊天同步", task_type=TYPE_SYNC)
        if not created:
            return {"status": "already_running", "msg": "同步任务已在运行中，请等待完成后再试"}
        asyncio.create_task(self._run_sync_worker(page_id, full_sync))
        return {"status": "started", "msg": "同步已启动"}

    async def sync_all_chats(self, page_id: str, full_sync: bool = False) -> AsyncGenerator[str, None]:
        """Progress generator for SSE. It starts the background worker if not already running."""
        self.task_key = f"chat_sync_{page_id}"
        created = await create_task_if_not_running(self.task_key, "聊天同步", task_type=TYPE_SYNC)
        if not created:
            yield "event: progress\ndata: " + json.dumps({
                "done": True, "error": True,
                "msg": "同步任务已在运行中，请等待完成后再试",
            }) + "\n\n"
            return

        asyncio.create_task(self._run_sync_worker(page_id, full_sync))
        await asyncio.sleep(0.1)

        last_update = ""
        while True:
            task = get_task(self.task_key)
            if not task:
                break

            updated = task.get("updated_at", "")
            if updated > last_update:
                legacy = {"msg": task.get("message", ""), "percent": task.get("progress", 0),
                          "done": task["status"] in (STATUS_SUCCESS, STATUS_FAILED), "updated_at": updated}
                result = task.get("result", {})
                if isinstance(result, dict):
                    legacy.update(result)
                yield "event: progress\ndata: " + json.dumps(legacy) + "\n\n"
                last_update = updated

            if task["status"] in (STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELED):
                break
            await asyncio.sleep(1)

    async def _run_sync_worker(self, page_id: str, full_sync: bool):
        """The actual background worker performing the sync in two phases."""
        if not full_sync:
            # Incremental mode: do NOT use `since` parameter for conversation discovery.
            # MAX(synced_at) is per-conversation, so using it as `since` would skip
            # conversations that haven't been synced recently but DO have new messages.
            # Instead, we scan all conversations and rely on `updated_time` comparison
            # to early-stop when we hit unchanged conversations (API returns newest-first).
            logger.info("[chat_sync] Incremental sync — scanning all conversations, using updated_time early-stop")
        else:
            logger.info("[chat_sync] FORCING FULL SYNC - Scanning all folders for Page: %s", page_id)

        status_msg = "阶段 1/2: 正在扫描会话列表..."
        update_task(self.task_key, message=status_msg, progress=5)

        self.conversations_synced = 0
        self.messages_synced = 0
        discovered_conv_set = set()

        target = page_id or "me"
        # Always scan all folders — incremental mode needs to check archive/other/spam too
        folders = ["inbox", "archive", "other", "spam"]
        
        try:
            # --- Phase 1: Discovery ---
            for folder in folders:
                after = ""
                stop_folder_sync = False
                logger.info("[chat_sync] Phase 1 - Scanning folder: %s", folder)

                while not stop_folder_sync:
                    params = {
                        "fields": "id,updated_time,unread_count,participants{id,name,picture.width(100).height(100)}",
                        "limit": 50,
                        "folder": folder
                    }
                    if after: params["after"] = after

                    payload = await self.fb._request('GET', f"{target}/conversations", params=params)
                    data = payload.get("data", [])
                    if not data:
                        break

                    for conv in data:
                        conv_id = conv["id"]
                        updated_time = conv.get("updated_time")

                        # In incremental mode, check if conversation has changed.
                        # API returns newest-first, so once we hit an unchanged one, stop.
                        has_changed = True
                        if not full_sync:
                            stored_updated = get_conversation_updated_time(conv_id)
                            if stored_updated == updated_time:
                                has_changed = False

                        if has_changed:
                            if conv_id not in discovered_conv_set:
                                discovered_conv_set.add(conv_id)
                                upsert_page_conversation(
                                    conv_id=conv_id,
                                    page_id=page_id,
                                    updated_time=updated_time,
                                    unread_count=conv.get("unread_count", 0),
                                    participants_json=json.dumps(conv.get("participants", {}))
                                )
                        else:
                            if not full_sync:
                                stop_folder_sync = True
                                break

                    if stop_folder_sync:
                        break

                    update_task(self.task_key,
                                message=f"阶段 1: 扫描目录 [{folder}] - 已发现 {len(discovered_conv_set)} 个活跃会话...",
                                progress=10)

                    paging = payload.get("paging", {})
                    after = paging.get("cursors", {}).get("after")
                    if not after:
                        break
            
            discovered_conv_ids = list(discovered_conv_set)
            total_convs = len(discovered_conv_ids)
            logger.info("[chat_sync] Phase 1 complete. Conversations to sync: %d", total_convs)
            
            if total_convs == 0:
                update_task(self.task_key, status=STATUS_SUCCESS,
                            message="没有发现需要同步的新会话。", progress=100,
                            result={"conversations": 0, "messages": 0})
                return

            # --- Phase 2: Message Sync ---
            status_msg = f"阶段 2/2: 正在同步 {total_convs} 个会话的消息..."
            update_task(self.task_key, message=status_msg, progress=20)
            
            pending = {
                asyncio.create_task(self._sync_messages_task(conv_id, full_sync=full_sync))
                for conv_id in discovered_conv_ids
            }
            
            while pending:
                done, pending = await asyncio.wait(pending, timeout=1.0, return_when=asyncio.FIRST_COMPLETED)
                processed = self.conversations_synced
                percent = 20 + int((processed / total_convs) * 80)
                update_task(self.task_key,
                            message=f"正在同步消息: {processed}/{total_convs} 会话...",
                            progress=min(99, percent),
                            result={"messages_synced": self.messages_synced})
                # Check for user cancellation
                task = get_task(self.task_key)
                if task and task["status"] == STATUS_CANCELED:
                    for t in pending:
                        t.cancel()
                    update_task(self.task_key, message=f"同步已停止（已处理 {processed}/{total_convs} 个会话）",
                                result={"conversations": self.conversations_synced, "messages": self.messages_synced})
                    return

            final_msg = f"同步完成！处理了 {total_convs} 个会话，新增/更新 {self.messages_synced} 条消息。"
            update_task(self.task_key, status=STATUS_SUCCESS, message=final_msg, progress=100,
                        result={"conversations": self.conversations_synced, "messages": self.messages_synced})

        except Exception as e:
            logger.error("[chat_sync] background worker failed: %s", e, exc_info=True)
            update_task(self.task_key, status=STATUS_FAILED, message=f"同步失败: {str(e)}", error=str(e))
        finally:
            await self.fb.close()

    async def _sync_messages_task(self, conv_id: str, full_sync: bool):
        async with self.semaphore:
            try:
                msg_count = await self._sync_messages_for_conversation(conv_id, full_sync=full_sync)
                self.messages_synced += msg_count
            except Exception as e:
                logger.warning("[chat_sync] Error syncing conversation %s: %s", conv_id, e)
            finally:
                self.conversations_synced += 1

    async def _sync_messages_for_conversation(self, conv_id: str, full_sync: bool) -> int:
        count = 0
        after = ""
        since_msg_ts = 0
        if not full_sync:
            since_msg_iso = get_latest_message_time(conv_id) or ""
            if since_msg_iso:
                since_msg_ts = self._iso_to_unix(since_msg_iso)
            
        while True:
            try:
                params = {
                    "limit": 100
                }
                if after: params["after"] = after
                if since_msg_ts: params["since"] = str(since_msg_ts)

                payload = await self.fb.fetch_messages(conv_id, **params)
                data = payload.get("data", [])
                if not data:
                    break
                
                batch_messages = []
                stop_message_sync = False
                
                for msg in data:
                    msg_id = msg["id"]
                    
                    # True Incremental Check: If message exists, stop fetching for this conversation
                    if not full_sync and check_message_exists(msg_id):
                        stop_message_sync = True
                        break
                        
                    text = self._filter_message_content(msg)
                    sender = msg.get("from", {})
                    batch_messages.append((
                        msg_id, conv_id, text, sender.get("id"), sender.get("name"), msg.get("created_time")
                    ))
                    count += 1
                
                if batch_messages:
                    bulk_upsert_conversation_messages(batch_messages)
                # Heartbeat: refresh task updated_at so stale detection knows we're alive
                heartbeat_task(self.task_key)
                
                if stop_message_sync:
                    break
                    
                paging = payload.get("paging", {})
                after = paging.get("cursors", {}).get("after")
                if not after:
                    break
            except Exception as e:
                logger.warning("[chat_sync] Failed to fetch messages for %s: %s", conv_id, e)
                break
        return count

    def _filter_message_content(self, msg: dict) -> str:
        text = msg.get("message", "")
        if msg.get("sticker"):
            text = text + " [表情]" if text else "[表情]"
        attachments = msg.get("attachments", {}).get("data", [])
        for att in attachments:
            mime = att.get("mime_type", "") or ""
            if "image" in mime: text += " [图片]"
            elif "audio" in mime or "voice" in mime: text += " [语音]"
            elif "video" in mime: text += " [视频]"
            else: text += " [文件]"
        return text.strip()
