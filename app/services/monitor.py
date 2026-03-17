from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.config import load_config
from app.repositories import (
    get_monitor,
    get_page_profile,
    get_post,
    has_replied,
    list_monitors,
    mark_replied,
    unmark_replied,
    update_monitor,
    upsert_comment,
)
from app.services.ai_reply import AIReplyService
from app.services.facebook import FacebookService

logger = logging.getLogger("uvicorn.error")

# How often the scheduler loop wakes up to check for due monitors (seconds).
_TICK_INTERVAL = 1


class MonitorService:
    def __init__(self):
        self._task: asyncio.Task[None] | None = None
        self._running_monitors: set[int] = set()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run_loop(), name="monitor-loop")
        logger.info("[monitor] background scheduler started (tick=%ss)", _TICK_INTERVAL)

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

    async def _safe_execute(self, monitor: dict[str, Any]) -> None:
        monitor_id = monitor["id"]
        self._running_monitors.add(monitor_id)
        try:
            await self._execute_monitor(monitor)
        except Exception as exc:
            logger.error("[monitor] monitor=%s failed: %s", monitor_id, exc)
            update_monitor(
                monitor_id,
                last_run_at=datetime.now(timezone.utc).isoformat(),
                last_run_status=f"ERROR: {str(exc)[:300]}",
            )
        finally:
            self._running_monitors.discard(monitor_id)

    async def _execute_monitor(self, monitor: dict[str, Any]) -> dict[str, Any]:
        monitor_id = monitor["id"]
        post_id = monitor["post_id"]
        max_depth = int(monitor.get("max_depth") or 1)

        logger.info("[monitor] running monitor=%s post=%s depth=%s", monitor_id, post_id, max_depth)

        post = get_post(post_id) or {}
        page_id = str(post.get("page_id", ""))
        if not page_id:
            raise RuntimeError(f"monitor={monitor_id} 关联帖子缺失 page_id")

        config = load_config(page_id=page_id)
        facebook = FacebookService(config)
        ai = AIReplyService(config)

        # Fetch fresh comments from Facebook
        comments = await facebook.fetch_comments_for_post(post_id, limit=200)

        # Upsert into local DB (incremental; don't delete existing)
        for comment in comments:
            upsert_comment(post_id, None, comment)

        # Gather post/page context once
        profile = get_page_profile(page_id=page_id) or {}

        replied_count = 0
        skipped_count = 0

        for comment in comments:
            # depth 1: top-level comments
            count, skipped = await self._process_comment(
                comment,
                post,
                profile,
                monitor_id,
                facebook=facebook,
                ai=ai,
                depth=1,
                max_depth=max_depth,
            )
            replied_count += count
            skipped_count += skipped

            # depth 2: direct replies
            if max_depth >= 2:
                for reply in comment.get("replies", {}).get("data", []):
                    count, skipped = await self._process_comment(
                        reply,
                        post,
                        profile,
                        monitor_id,
                        facebook=facebook,
                        ai=ai,
                        depth=2,
                        max_depth=max_depth,
                        parent_message=comment.get("message", ""),
                    )
                    replied_count += count
                    skipped_count += skipped

        status_msg = f"OK: 已回复 {replied_count} 条，已跳过 {skipped_count} 条"
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
        return {"replied": replied_count, "skipped": skipped_count}

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
        max_depth: int,
        parent_message: str = "",
    ) -> tuple[int, int]:
        if depth > max_depth:
            return 0, 0

        comment_id = comment.get("id", "")
        if not comment_id:
            return 0, 0

        if has_replied(comment_id):
            page_id = str(profile.get("page_id") or post.get("page_id") or facebook.config.page_id or "")
            still_has_page_reply = await self._comment_has_page_reply(
                comment=comment,
                page_id=page_id,
                facebook=facebook,
            )
            if still_has_page_reply:
                return 0, 1  # already replied, skip

            # Local dedupe record is stale (reply was deleted manually), allow re-reply.
            try:
                unmark_replied(comment_id)
            except Exception as exc:
                logger.warning(
                    "[monitor] stale replied record cleanup failed comment=%s: %s",
                    comment_id,
                    exc,
                )
                return 0, 1

        author = comment.get("from", {})
        author_name = author.get("name", "匿名用户")
        comment_message = comment.get("message", "")

        try:
            reply_message = await ai.generate_reply(
                page_name=profile.get("name", ""),
                post_message=post.get("message", ""),
                comment_message=comment_message,
                comment_author=author_name,
                parent_comment_message=parent_message,
            )
            await facebook.send_reply(comment_id, reply_message)
        except Exception as exc:
            logger.warning("[monitor] failed to reply to comment=%s: %s", comment_id, exc)
            return 0, 0

        try:
            mark_replied(comment_id, post.get("id", ""), monitor_id, reply_message)
        except Exception as exc:
            # Reply was already sent successfully; keep count as replied to avoid misleading stats.
            logger.warning(
                "[monitor] reply sent but failed to persist replied record comment=%s: %s",
                comment_id,
                exc,
            )

        logger.info(
            "[monitor] replied to comment=%s author=%s depth=%s",
            comment_id,
            author_name,
            depth,
        )
        return 1, 0

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
