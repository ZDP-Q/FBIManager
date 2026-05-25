from __future__ import annotations

import asyncio
import json
import logging
import math
import random
from datetime import datetime, timezone
from typing import Any

from app.config import load_config
from app.repositories import (
    count_pending_comments,
    get_latest_comment_time,
    get_monitor,
    get_page_profile,
    get_post,
    get_video_analysis,
    has_replied,
    list_comments_by_post_ids,
    list_monitors,
    list_pending_comments,
    list_replied_for_post,
    mark_comments_screened,
    mark_replied,
    unmark_replied,
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
        logger.info("[monitor] background scheduler stopped")

    async def run_monitor_now(self, monitor_id: int) -> dict[str, Any]:
        """Trigger a single monitor immediately (used by API)."""
        monitor = get_monitor(monitor_id)
        if monitor is None:
            raise ValueError(f"Monitor {monitor_id} not found")
        return await self._execute_monitor(monitor)

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

            asyncio.create_task(
                self._safe_execute(monitor),
                name=f"monitor-{monitor_id}",
            )

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
                from app.config import load_config
                
                cfg = load_config(account_id=account["id"])
                sync_svc = SyncService(cfg)
                
                # Fetch recent posts (limit 15 for discovery)
                logger.info("[monitor] auto-discovery: fetching posts for account %s (%s)", account["name"], page_id)
                async for step in sync_svc.sync_all_gen(post_limit=15):
                    pass
                
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
        try:
            async with self._semaphore:
                await self._execute_monitor(monitor)
        except Exception as exc:
            logger.exception("[monitor] monitor=%s failed with exception", monitor_id)
            update_monitor(
                monitor_id,
                last_run_at=datetime.now(timezone.utc).isoformat(),
                last_run_status=f"ERROR: {type(exc).__name__}: {str(exc)[:300]}",
            )
        finally:
            self._running_monitors.discard(monitor_id)

    async def _execute_monitor(self, monitor: dict[str, Any]) -> dict[str, Any]:
        monitor_id = monitor["id"]
        post_id = monitor["post_id"]

        logger.info("[monitor] running monitor=%s post=%s (unlimited depth mode)", monitor_id, post_id)

        post = get_post(post_id) or {}
        # 优先从数据库中获取规范化的数字 Page ID
        page_id = str(post.get("page_id", ""))

        # 加载视频分析内容作为 AI 上下文
        video_analysis_ctx = ""
        if post.get("type") == "video":
            va = get_video_analysis(post_id)
            if va:
                raw = va.get("content", "")
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict) and all(k in parsed for k in ("location", "behavior", "environment")):
                        video_analysis_ctx = f"拍摄地点：{parsed['location']}；人物行为：{parsed['behavior']}；场景环境：{parsed['environment']}"
                except (json.JSONDecodeError, TypeError):
                    if raw.strip():
                        video_analysis_ctx = raw.strip()
        if not page_id:
            raise RuntimeError(f"monitor={monitor_id} 关联帖子缺失 page_id")

        config = load_config(page_id=page_id)
        facebook = FacebookService(config)
        ai = AIReplyService(config)

        # Step 0: 优先使用本地已同步的评论；若无则触发内容中心同步
        local_comments_map = list_comments_by_post_ids([post_id])
        local_comments = local_comments_map.get(post_id, [])

        if not local_comments:
            logger.info("[monitor] monitor=%s: 本地无评论，触发内容中心同步", monitor_id)
            update_monitor(monitor_id, last_run_status="首次运行，正在同步帖子评论...")
            from app.services.sync import SyncService
            sync_svc = SyncService(config)
            await sync_svc.sync_post(post_id)
            local_comments_map = list_comments_by_post_ids([post_id])
            local_comments = local_comments_map.get(post_id, [])
            logger.info("[monitor] monitor=%s: 同步完成，本地现有 %d 条顶层评论", monitor_id, len(local_comments))

        # Step 1: 以本地最新评论的时间戳为基准，增量拉取新评论
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
        else:
            logger.info("[monitor] monitor=%s: 无本地评论时间戳，跳过远程拉取（已通过同步获取）", monitor_id)

        # Step 2: 检查待处理评论是否达到批量处理门槛
        pending_count = count_pending_comments(post_id)
        is_first_cycle = not monitor.get("last_run_at")
        _BATCH_THRESHOLD = 50

        if not is_first_cycle and pending_count < _BATCH_THRESHOLD:
            logger.info("[monitor] monitor=%s: 待处理 %d < 门槛 %d，跳过本周期",
                        monitor_id, pending_count, _BATCH_THRESHOLD)
            update_monitor(
                monitor_id,
                last_run_at=datetime.now(timezone.utc).isoformat(),
                last_run_status=f"待处理 {pending_count}/{_BATCH_THRESHOLD}，等待下一周期",
            )
            return {"replied": 0, "skipped": 0, "scored": 0, "total": pending_count, "already": 0}

        # Step 3: 重载本地评论、归一化、展平
        local_comments_map = list_comments_by_post_ids([post_id])
        local_comments = local_comments_map.get(post_id, [])

        def _normalize(cs: list[dict[str, Any]]) -> list[dict[str, Any]]:
            result: list[dict[str, Any]] = []
            for c in cs:
                item = dict(c)
                item["from"] = {
                    "id": str(c.get("author_id", "")),
                    "name": c.get("author_name", "匿名用户"),
                }
                replies = item.pop("replies", [])
                if replies:
                    item["replies"] = {"data": _normalize(replies)}
                else:
                    item["replies"] = {}
                result.append(item)
            return result

        comments = _normalize(local_comments)

        flat_comments: list[dict[str, Any]] = []
        def _flatten(cs: list[dict[str, Any]], depth: int, parent_msg: str = ""):
            for c in cs:
                flat_comments.append({"comment": c, "depth": depth, "parent_message": parent_msg})
                replies = c.get("replies", {}).get("data", [])
                if replies:
                    _flatten(replies, depth + 1, c.get("message", ""))
        _flatten(comments, 1)

        # id -> flat_item 映射，供回复阶段查找
        flat_map: dict[str, dict[str, Any]] = {item["comment"]["id"]: item for item in flat_comments}

        # 获取主页资料
        profile = get_page_profile(page_id=page_id) or {}
        canonical_page_id = str(profile.get("page_id") or page_id)

        # Step 4: 获取待处理评论，过滤自己的 + 已回复的
        pending = list_pending_comments(post_id)
        scorable: list[dict[str, Any]] = []
        already_replied_count = 0
        for c in pending:
            if c["id"] in flat_map:
                flat_item = flat_map[c["id"]]
                author = flat_item["comment"].get("from", {})
                author_id = str(author.get("id") or "")
                author_name = author.get("name", "")
                if author_id and canonical_page_id and author_id == canonical_page_id:
                    continue
                if author_name and profile.get("name") and author_name == profile.get("name"):
                    continue
            if has_replied(c["id"]):
                already_replied_count += 1
                continue
            scorable.append(c)

        stats = {"replied": 0, "skipped": 0, "scored": len(scorable), "total": len(pending), "already": already_replied_count}

        if not scorable:
            mark_comments_screened([c["id"] for c in pending])
            status_msg = f"无有效待评分评论 | 总待处理 {stats['total']} | 已处理 {stats['already']}"
            finished_at = datetime.now().astimezone()
            update_monitor(
                monitor_id,
                last_run_at=finished_at.astimezone(timezone.utc).isoformat(),
                last_run_status=status_msg,
            )
            logger.info("[%s] [monitor] monitor=%s done: %s",
                        finished_at.strftime("%Y-%m-%d %H:%M:%S %z"), monitor_id, status_msg)
            return stats

        # Step 5: 批量评分
        logger.info("[monitor] monitor=%s: 正在批量评分 %d 条评论...", monitor_id, len(scorable))
        update_monitor(
            monitor_id,
            last_run_at=datetime.now(timezone.utc).isoformat(),
            last_run_status=f"正在批量评分 {len(scorable)} 条评论...",
        )
        scored = await ai.score_comments(
            post_message=post.get("message", ""),
            video_analysis=video_analysis_ctx,
            comments=scorable,
        )

        # Step 6: 按评分排序，取前 30%
        scored.sort(key=lambda x: x["score"], reverse=True)
        top_n = max(1, math.ceil(len(scored) * 0.3))
        to_reply = scored[:top_n]
        logger.info("[monitor] monitor=%s: 评分完成，%d 条参与，前 30%% = %d 条，分数范围 %d-%d",
                    monitor_id, len(scored), len(to_reply),
                    to_reply[0]["score"], to_reply[-1]["score"])

        # Step 7: 标记所有 pending 为已处理（包括被筛掉的）
        mark_comments_screened([c["id"] for c in pending])

        # Step 8: 并发回复前 30%
        update_monitor(
            monitor_id,
            last_run_at=datetime.now(timezone.utc).isoformat(),
            last_run_status=f"评分完成，正在回复前 {len(to_reply)} 条...",
        )

        previous_replies = list_replied_for_post(post_id, limit=20)
        random.shuffle(to_reply)

        _BATCH_SIZE = 5
        for batch_start in range(0, len(to_reply), _BATCH_SIZE):
            if batch_start > 0:
                delay = random.uniform(10, 45)
                await asyncio.sleep(delay)

            batch = to_reply[batch_start:batch_start + _BATCH_SIZE]
            batch_end = min(batch_start + _BATCH_SIZE, len(to_reply))
            update_monitor(
                monitor_id,
                last_run_at=datetime.now(timezone.utc).isoformat(),
                last_run_status=f"回复中 {batch_start + 1}-{batch_end}/{len(to_reply)}（总 {stats['total']} | 评分 {stats['scored']} | 已回 {stats['replied']}）",
            )

            async def _reply_one(scored_item: dict[str, Any]) -> tuple[int, int, int]:
                flat_item = flat_map.get(scored_item["id"])
                if flat_item is None:
                    return 0, 0, 0
                return await self._process_comment(
                    flat_item["comment"],
                    post,
                    profile,
                    monitor_id,
                    facebook=facebook,
                    ai=ai,
                    depth=flat_item["depth"],
                    parent_message=flat_item["parent_message"],
                    canonical_page_id=canonical_page_id,
                    previous_replies=previous_replies,
                    video_analysis=video_analysis_ctx,
                )

            results = await asyncio.gather(*[_reply_one(item) for item in batch])
            for replied, skipped, already in results:
                stats["replied"] += replied
                stats["skipped"] += skipped
                stats["already"] += already

        status_msg = f"总待处理 {stats['total']} | 评分 {stats['scored']} | 回复前{top_n} | 新回复 {stats['replied']} | 跳过 {stats['skipped']} | 已处理 {stats['already']}"
        finished_at = datetime.now().astimezone()
        update_monitor(
            monitor_id,
            last_run_at=finished_at.astimezone(timezone.utc).isoformat(),
            last_run_status=status_msg,
        )
        logger.info(
            "[%s] [monitor] monitor=%s done: %s",
            finished_at.strftime("%Y-%m-%d %H:%M:%S %z"),
            monitor_id,
            status_msg,
        )
        return stats

    async def _process_comment(
        self,
        comment: dict[str, Any],
        post: dict[str, Any],
        profile: dict[str, Any],
        monitor_id: int,
        *,
        facebook: FacebookService,
        ai: AIReplyService,
        depth: int,
        parent_message: str = "",
        canonical_page_id: str = "",
        previous_replies: list[dict[str, Any]] | None = None,
        video_analysis: str = "",
    ) -> tuple[int, int, int]:
        """Returns (replied, skipped, already) — already=1 when skipped because previously replied."""

        comment_id = comment.get("id", "")
        if not comment_id:
            return 0, 0, 0

        author = comment.get("from", {})
        author_name = author.get("name", "")
        page_name = profile.get("name", "")

        # 1. 去重检查：是否已回复过
        if has_replied(comment_id):
            still_has_page_reply = await self._comment_has_page_reply(
                comment=comment,
                page_id=canonical_page_id,
                facebook=facebook,
            )
            if still_has_page_reply:
                return 0, 0, 1  # 历史已回复，跳过

            try:
                unmark_replied(comment_id)
            except Exception:
                return 0, 0, 1

        if await self._comment_has_page_reply(comment=comment, page_id=canonical_page_id, facebook=facebook):
            return 0, 1, 0  # Facebook 已有回复，跳过

        # 2. 生成并发送回复（AI 筛选已在第一轮完成）
        comment_message = comment.get("message", "")
        try:
            reply_message = await ai.generate_reply(
                page_name=page_name,
                post_message=post.get("message", ""),
                comment_message=comment_message,
                comment_author=author_name or "匿名用户",
                parent_comment_message=parent_message,
                previous_replies=previous_replies,
                video_analysis=video_analysis,
            )
            await facebook.send_reply(comment_id, reply_message)
        except Exception as exc:
            logger.warning("[monitor] failed to reply to comment=%s: %s", comment_id, exc)
            return 0, 0, 0

        # 3. 记录已回复
        try:
            mark_replied(comment_id, post.get("id", ""), monitor_id, reply_message)
        except Exception as exc:
            logger.warning("[monitor] reply sent but failed to persist record: %s", exc)

        logger.info("[monitor] replied to comment=%s author=%s depth=%s", comment_id, author_name, depth)
        return 1, 0, 0

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
