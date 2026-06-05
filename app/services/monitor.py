from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Any

from app.config import load_config
from app.repositories import (
    count_all_comments,
    count_replied_comments,
    get_latest_comment_time,
    get_monitor,
    get_page_profile,
    get_post,
    has_replied,
    list_comments_by_post_ids,
    list_monitors,
    list_replied_for_post,
    list_unreplied_comments,
    mark_replied,
    update_monitor,
    upsert_comment,
    get_auto_monitor_config,
    list_auto_monitor_schedules,
    mark_auto_monitor_triggered,
    list_accounts,
    create_monitor,
    list_monitored_post_ids,
)
from app.services.ai_reply import AIReplyService
from app.services.attachments import download_comment_attachments
from app.services.facebook import FacebookService

logger = logging.getLogger("uvicorn.error")

# How often the scheduler loop wakes up to check for due monitors (seconds).
_TICK_INTERVAL = 1
# Limit maximum concurrent monitor tasks to avoid network congestion and API rate limits.
_MAX_CONCURRENT_MONITORS = 5


class MonitorService:
    def __init__(self):
        self._task: asyncio.Task[None] | None = None
        self._running_monitors: set[int] = set()
        self._spawned_tasks: set[asyncio.Task] = set()
        self._semaphore = asyncio.Semaphore(_MAX_CONCURRENT_MONITORS)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run_loop(), name="monitor-loop")
        logger.info("[monitor] background scheduler started (tick=%ss, max_concurrent=%s)", _TICK_INTERVAL, _MAX_CONCURRENT_MONITORS)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Cancel all spawned monitor sub-tasks
        for t in self._spawned_tasks:
            if not t.done():
                t.cancel()
        if self._spawned_tasks:
            await asyncio.gather(*self._spawned_tasks, return_exceptions=True)
        self._spawned_tasks.clear()
        logger.info("[monitor] background scheduler stopped")

    async def run_monitor_now(self, monitor_id: int) -> dict[str, Any]:
        """Trigger a single monitor immediately (used by API)."""
        monitor = get_monitor(monitor_id)
        if monitor is None:
            raise ValueError(f"Monitor {monitor_id} not found")
        post = get_post(monitor["post_id"]) or {}
        page_id = str(post.get("page_id", ""))
        config = load_config(page_id=page_id)
        facebook = FacebookService(config)
        try:
            return await self._execute_monitor(monitor, facebook)
        finally:
            await facebook.close()

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    async def _run_loop(self) -> None:
        while True:
            try:
                await self._tick()
            except Exception as exc:
                logger.exception("[monitor] unexpected error in tick: %s", exc)
            await asyncio.sleep(_TICK_INTERVAL)

    async def _tick(self) -> None:
        await self._check_auto_monitor_schedules()
        
        monitors = list_monitors()
        now = datetime.now(timezone.utc)
        for monitor in monitors:
            if not monitor["enabled"]:
                continue
            monitor_id = monitor["id"]
            if monitor_id in self._running_monitors:
                continue  # skip if already running

            last_run = monitor.get("last_run_at")
            if last_run:
                try:
                    last_run_dt = datetime.fromisoformat(last_run)
                    if last_run_dt.tzinfo is None:
                        last_run_dt = last_run_dt.replace(tzinfo=timezone.utc)
                    elapsed = (now - last_run_dt).total_seconds()
                    if elapsed < monitor["interval_seconds"]:
                        continue
                except ValueError:
                    pass  # bad timestamp, run anyway

            t = asyncio.create_task(
                self._safe_execute(monitor),
                name=f"monitor-{monitor_id}",
            )
            self._spawned_tasks.add(t)
            t.add_done_callback(self._spawned_tasks.discard)

    async def _check_auto_monitor_schedules(self) -> None:
        config = get_auto_monitor_config()
        if not config.get("enabled"):
            return

        # Use system local time for comparison with user-entered "HH:MM"
        now = datetime.now()
        current_time_str = now.strftime("%H:%M")
        current_minute_key = now.strftime("%Y-%m-%d %H:%M")

        schedules = list_auto_monitor_schedules()
        for schedule in schedules:
            if not schedule.get("enabled"):
                continue
            if schedule["trigger_time"] == current_time_str:
                if schedule.get("last_triggered_at") != current_minute_key:
                    logger.info("[monitor] auto-monitor schedule triggered for %s", current_time_str)
                    # Use a task to not block the main loop
                    asyncio.create_task(self._run_auto_discovery(config["max_posts"]))
                    mark_auto_monitor_triggered(schedule["id"], current_minute_key)

    async def _run_auto_discovery(self, max_posts: int) -> None:
        logger.info("[monitor] starting auto post discovery (max_posts=%s)", max_posts)
        accounts = list_accounts()
        for account in accounts:
            # We check for page_access_token to ensure account is somewhat valid
            if not account.get("page_access_token"):
                continue
            
            try:
                page_id = account["page_id"]
                from app.services.sync import SyncService

                cfg = load_config(account_id=account["id"])
                sync_svc = SyncService(cfg)
                
                # Fetch recent posts (limit 15 for discovery)
                logger.info("[monitor] auto-discovery: fetching posts for account %s (%s)", account["name"], page_id)
                await sync_svc.sync_all(post_limit=15)
                
                # After sync, get all post IDs for this page and their monitor status
                from app.repositories import list_posts
                posts = list_posts(page_id=page_id, limit=30)
                monitored_ids = list_monitored_post_ids(page_id)
                
                current_monitor_count = len(monitored_ids)
                if current_monitor_count >= max_posts:
                    logger.info("[monitor] account %s already has %s monitors (limit %s), skipping discovery", 
                                account["name"], current_monitor_count, max_posts)
                    continue
                
                newly_added = 0
                for post in posts:
                    if post["id"] not in monitored_ids:
                        create_monitor(post["id"])
                        monitored_ids.add(post["id"])
                        newly_added += 1
                        current_monitor_count += 1
                        if current_monitor_count >= max_posts:
                            break
                
                if newly_added > 0:
                    logger.info("[monitor] account %s: automatically added %s new monitors", account["name"], newly_added)
                
            except Exception as exc:
                logger.error("[monitor] auto-discovery failed for account %s: %s", account.get("name"), exc)

    async def _safe_execute(self, monitor: dict[str, Any]) -> None:
        monitor_id = monitor["id"]
        self._running_monitors.add(monitor_id)
        update_monitor(
            monitor_id,
            last_run_at=datetime.now(timezone.utc).isoformat(),
            last_run_status="等待执行...",
        )
        post = get_post(monitor["post_id"]) or {}
        page_id = str(post.get("page_id", ""))
        config = load_config(page_id=page_id)
        facebook = FacebookService(config)
        try:
            async with self._semaphore:
                await self._execute_monitor(monitor, facebook)
        except Exception as exc:
            logger.exception("[monitor] monitor=%s failed with exception", monitor_id)
            update_monitor(
                monitor_id,
                last_run_at=datetime.now(timezone.utc).isoformat(),
                last_run_status=f"ERROR: {type(exc).__name__}: {str(exc)[:300]}",
            )
        finally:
            await facebook.close()
            self._running_monitors.discard(monitor_id)

    async def _execute_monitor(self, monitor: dict[str, Any], facebook: FacebookService) -> dict[str, Any]:
        monitor_id = monitor["id"]
        post_id = monitor["post_id"]

        logger.info("[monitor] running monitor=%s post=%s", monitor_id, post_id)

        post = get_post(post_id) or {}
        page_id = str(post.get("page_id", ""))
        if not page_id:
            raise RuntimeError(f"monitor={monitor_id} 关联帖子缺失 page_id")

        config = load_config(page_id=page_id)

        # Step 0: 本地无评论时先同步
        local_comments_map = list_comments_by_post_ids([post_id])
        local_comments = local_comments_map.get(post_id, [])

        if not local_comments:
            logger.info("[monitor] monitor=%s: 本地无评论，触发内容中心同步", monitor_id)
            update_monitor(monitor_id, last_run_status="首次运行，正在同步帖子评论...")
            from app.services.sync import SyncService
            sync_svc = SyncService(config)
            await sync_svc.sync_post(post_id)

        # Step 1: 增量拉取新评论
        latest_time = get_latest_comment_time(post_id)
        since_ts: int | None = None
        if latest_time:
            try:
                dt = datetime.fromisoformat(latest_time)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                since_ts = int(dt.timestamp())
            except ValueError:
                pass

        if since_ts:
            logger.info("[monitor] monitor=%s: 增量拉取 since=%s", monitor_id, latest_time)
            new_comments, _ = await facebook.fetch_comments_for_post(
                post_id, limit=200, max_depth=3, since=since_ts
            )
            if new_comments:
                logger.info("[monitor] monitor=%s: 获取到 %d 条新评论", monitor_id, len(new_comments))
                for comment in new_comments:
                    upsert_comment(post_id, None, comment)

                # 下载附件
                async def _dl_attachments(cs):
                    for c in cs:
                        await download_comment_attachments(c, facebook)
                        for reply in c.get("replies", {}).get("data", []):
                            await _dl_attachments([reply])
                await _dl_attachments(new_comments)
        else:
            logger.info("[monitor] monitor=%s: 无本地评论时间戳，跳过远程拉取", monitor_id)

        # Step 2: 计算回复目标 = 总评论数的 10%（向下取整）
        profile = get_page_profile(page_id=page_id) or {}
        canonical_page_id = str(profile.get("page_id") or page_id)

        total_comments = count_all_comments(post_id)
        replied_count = count_replied_comments(post_id)
        target = total_comments // 10  # 10%, floor
        need = target - replied_count

        status_msg = f"总评论 {total_comments} | 目标 {target} | 已回复 {replied_count} | 需回复 {max(need, 0)}"
        logger.info("[monitor] monitor=%s: %s", monitor_id, status_msg)
        update_monitor(monitor_id, last_run_at=datetime.now(timezone.utc).isoformat(), last_run_status=status_msg)

        if need <= 0:
            logger.info("[monitor] monitor=%s: 已达到 10%% 目标，跳过", monitor_id)
            return {"replied": 0, "skipped": 0, "total": total_comments, "target": target, "already": replied_count}

        # Step 3: 从未回复的评论中随机选取 need 条
        candidates = list_unreplied_comments(post_id, exclude_author_id=canonical_page_id)
        if not candidates:
            logger.info("[monitor] monitor=%s: 无可回复评论", monitor_id)
            return {"replied": 0, "skipped": 0, "total": total_comments, "target": target, "already": replied_count}

        to_reply = candidates[:need]
        logger.info("[monitor] monitor=%s: 随机选取 %d 条评论进行回复", monitor_id, len(to_reply))
        update_monitor(
            monitor_id,
            last_run_at=datetime.now(timezone.utc).isoformat(),
            last_run_status=f"正在回复 {len(to_reply)} 条评论...",
        )

        # Step 4: 逐条回复
        ai = AIReplyService(config)
        previous_replies = list_replied_for_post(post_id, limit=20)
        replied_new = 0
        skipped = 0

        for i, comment in enumerate(to_reply):
            comment_id = comment.get("id", "")
            if not comment_id:
                continue

            # 远程去重：检查 Facebook 上是否已有主页回复
            if await self._comment_has_page_reply(
                comment={"id": comment_id, "replies": {}},
                page_id=canonical_page_id,
                facebook=facebook,
            ):
                skipped += 1
                mark_replied(comment_id, post_id, monitor_id, "")
                continue

            comment_message = comment.get("message", "")
            author_name = comment.get("author_name", "匿名用户")
            try:
                reply_message = await ai.generate_reply(
                    page_name=profile.get("name", ""),
                    post_message=post.get("message", ""),
                    comment_message=comment_message,
                    comment_author=author_name,
                    previous_replies=previous_replies,
                )
                await facebook.send_reply(comment_id, reply_message)
                mark_replied(comment_id, post_id, monitor_id, reply_message)
                replied_new += 1
                previous_replies = list_replied_for_post(post_id, limit=20)
            except Exception as exc:
                logger.warning("[monitor] failed to reply to comment=%s: %s", comment_id, exc)

            # 批间延迟
            if i < len(to_reply) - 1:
                await asyncio.sleep(random.uniform(10, 45))

        status_msg = f"总评论 {total_comments} | 目标 {target} | 已回复 {replied_count + replied_new} | 本次新回复 {replied_new} | 跳过 {skipped}"
        finished_at = datetime.now().astimezone()
        update_monitor(
            monitor_id,
            last_run_at=finished_at.astimezone(timezone.utc).isoformat(),
            last_run_status=status_msg,
        )
        logger.info("[%s] [monitor] monitor=%s done: %s",
                    finished_at.strftime("%Y-%m-%d %H:%M:%S %z"), monitor_id, status_msg)
        return {"replied": replied_new, "skipped": skipped, "total": total_comments, "target": target, "already": replied_count}

    async def _comment_has_page_reply(
        self,
        *,
        comment: dict[str, Any],
        page_id: str,
        facebook: FacebookService,
    ) -> bool:
        if not page_id:
            return True

        # Use replies from the current fetch first to avoid an extra request in common cases.
        for reply in comment.get("replies", {}).get("data", []):
            if str(reply.get("from", {}).get("id", "")) == page_id:
                return True

        comment_id = str(comment.get("id", ""))
        if not comment_id:
            return True

        try:
            latest_replies = await facebook.fetch_replies_for_comment(comment_id, limit=100)
        except Exception as exc:
            # Fail safe: if we cannot verify remote state, keep dedupe to avoid accidental spam.
            logger.warning(
                "[monitor] unable to verify existing replies for comment=%s: %s",
                comment_id,
                exc,
            )
            return True

        for reply in latest_replies:
            if str(reply.get("from", {}).get("id", "")) == page_id:
                return True

        return False
