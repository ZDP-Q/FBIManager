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
    get_monitor,
    get_page_profile,
    get_post,
    get_screened_comment_ids,
    get_video_analysis,
    has_replied,
    list_comments_by_post_ids,
    list_monitors,
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

        # Step 1: 增量拉取上次运行之后的新评论
        since_ts: int | None = None
        last_run_at_raw = monitor.get("last_run_at")
        if last_run_at_raw:
            try:
                dt = datetime.fromisoformat(str(last_run_at_raw))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                since_ts = int(dt.timestamp())
            except ValueError:
                pass

        if since_ts:
            logger.info("[monitor] monitor=%s: 增量拉取新评论 since=%s", monitor_id, last_run_at_raw)
            new_comments, cursors = await facebook.fetch_comments_for_post(
                post_id, limit=200, max_depth=3, since=since_ts
            )
            if new_comments:
                logger.info("[monitor] monitor=%s: 获取到 %d 条新评论", monitor_id, len(new_comments))
                for comment in new_comments:
                    upsert_comment(post_id, None, comment)
                # 重载本地评论以包含新增的
                local_comments_map = list_comments_by_post_ids([post_id])
                local_comments = local_comments_map.get(post_id, [])
        else:
            logger.info("[monitor] monitor=%s: 首次监控运行，直接使用本地已同步评论", monitor_id)

        # Step 2: 将本地 DB 格式转换为 Facebook API 格式（供下游统一处理）
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
        logger.info("[monitor] monitor=%s: 本地共 %d 条评论待处理", monitor_id, len(comments))

        # 获取主页资料，用于获取主页名称和确认数字 ID
        logger.info("[monitor] monitor=%s: 正在获取主页资料...", monitor_id)
        profile = get_page_profile(page_id=page_id) or {}
        # 最终确认使用的数字 Page ID
        canonical_page_id = str(profile.get("page_id") or page_id)

        stats = {"replied": 0, "skipped": 0, "screened": 0, "total": 0, "already": 0}

        # 立即更新运行状态，避免 UI 显示"从未执行"
        update_monitor(
            monitor_id,
            last_run_at=datetime.now(timezone.utc).isoformat(),
            last_run_status="运行中...",
        )

        # 获取该帖子的已回复历史，供 AI 避免重复内容
        previous_replies = list_replied_for_post(post_id, limit=20)

        # 展平所有评论为 flat list（保留 depth 和 parent_message）
        logger.info("[monitor] monitor=%s: 正在展平评论...", monitor_id)
        flat_comments: list[dict[str, Any]] = []
        def _flatten(cs: list[dict[str, Any]], depth: int, parent_msg: str = ""):
            for c in cs:
                flat_comments.append({"comment": c, "depth": depth, "parent_message": parent_msg})
                replies = c.get("replies", {}).get("data", [])
                if replies:
                    _flatten(replies, depth + 1, c.get("message", ""))
        _flatten(comments, 1)

        # 过滤掉主页自己的评论 + 已回复过的评论 + 已筛选过的评论
        candidates = []
        already_replied_count = 0
        screened_ids = get_screened_comment_ids(post_id)
        for item in flat_comments:
            c = item["comment"]
            author = c.get("from", {})
            author_id = str(author.get("id") or "")
            author_name = author.get("name", "")
            if author_id and canonical_page_id and author_id == canonical_page_id:
                continue
            if author_name and profile.get("name") and author_name == profile.get("name"):
                continue
            # 跳过已在 replied_comments 表中的评论（节省 AI 筛选调用）
            comment_id = c.get("id", "")
            if comment_id and has_replied(comment_id):
                already_replied_count += 1
                continue
            # 跳过已筛选过的评论（历史 SKIP 或 REPLY 结果保持有效）
            if comment_id and comment_id in screened_ids:
                continue
            candidates.append(item)

        stats["already"] = already_replied_count
        stats["total"] = len(candidates) + already_replied_count
        logger.info("[monitor] monitor=%s: %d 条候选评论（%d 条已处理），开始 AI 筛选...",
                    monitor_id, len(candidates), already_replied_count)

        # AI 批量筛选（并发，每条评论独立筛选）
        async def _screen_one(item: dict[str, Any]) -> dict[str, Any] | None:
            c = item["comment"]
            msg = c.get("message", "")
            author = c.get("from", {}).get("name", "匿名用户")
            worth = await ai.screen_comment(comment_message=msg, comment_author=author)
            return item if worth else None

        screen_results = await asyncio.gather(*[_screen_one(item) for item in candidates])
        passed = [item for item in screen_results if item is not None]
        screened_count = len(candidates) - len(passed)
        stats["screened"] = screened_count

        # 标记所有候选评论为已筛选，下一周期不再重复筛选
        all_candidate_ids = [item["comment"]["id"] for item in candidates if item["comment"].get("id")]
        mark_comments_screened(all_candidate_ids)

        # 限制回复数量不超过总评论的 30%
        max_replies = max(1, math.ceil(len(candidates) * 0.3))
        original_passed = len(passed)
        if len(passed) > max_replies:
            passed = passed[:max_replies]

        logger.info("[monitor] monitor=%s: AI 筛选完成，通过 %d 条，跳过 %d 条，最终回复 %d 条 (上限 %d)",
                    monitor_id, original_passed, screened_count, len(passed), max_replies)

        # 更新状态
        update_monitor(
            monitor_id,
            last_run_at=datetime.now(timezone.utc).isoformat(),
            last_run_status=f"筛选完成，{len(passed)} 条待回复（已处理 {stats['already']} | 筛选 {stats['screened']}）...",
        )

        # 随机打乱通过筛选的评论
        random.shuffle(passed)

        # 第二轮：5 条并发回复，批次间随机延迟
        _BATCH_SIZE = 5
        total_to_reply = len(passed)
        replied_total = 0
        for batch_start in range(0, total_to_reply, _BATCH_SIZE):
            if batch_start > 0:
                delay = random.uniform(10, 45)
                await asyncio.sleep(delay)

            batch = passed[batch_start:batch_start + _BATCH_SIZE]
            batch_end = min(batch_start + _BATCH_SIZE, total_to_reply)
            update_monitor(
                monitor_id,
                last_run_at=datetime.now(timezone.utc).isoformat(),
                last_run_status=f"回复中 {batch_start + 1}-{batch_end}/{total_to_reply}（总评论 {stats['total']} | 已处理 {stats['already']} | 新回复 {stats['replied']} | 跳过 {stats['skipped']}）",
            )

            async def _reply_one(item: dict[str, Any]) -> tuple[int, int, int]:
                return await self._process_comment(
                    item["comment"],
                    post,
                    profile,
                    monitor_id,
                    facebook=facebook,
                    ai=ai,
                    depth=item["depth"],
                    parent_message=item["parent_message"],
                    canonical_page_id=canonical_page_id,
                    previous_replies=previous_replies,
                    video_analysis=video_analysis_ctx,
                )

            results = await asyncio.gather(*[_reply_one(item) for item in batch])
            for replied, skipped, already in results:
                stats["replied"] += replied
                stats["skipped"] += skipped
                stats["already"] += already

        status_msg = f"总评论 {stats['total']} | 筛选掉 {stats['screened']} | 待回复 {len(passed)} | 已处理 {stats['already']} | 新回复 {stats['replied']} | 跳过 {stats['skipped']}"
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
