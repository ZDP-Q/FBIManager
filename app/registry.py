"""Holds application-wide service singletons to avoid circular imports."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.monitor import MonitorService

import time

_monitor_service: "MonitorService | None" = None
_task_statuses: dict[str, dict] = {}
_analyzing_posts: dict[str, dict] = {}


def set_monitor_service(svc: "MonitorService") -> None:
    global _monitor_service
    _monitor_service = svc


def get_monitor_service() -> "MonitorService":
    if _monitor_service is None:
        raise RuntimeError("MonitorService not initialized")
    return _monitor_service


def update_task_status(task_name: str, status: dict) -> None:
    """Updates the global status for a named task (e.g., 'post_sync', 'chat_sync')."""
    status["updated_at"] = time.time()
    _task_statuses[task_name] = status


def get_task_status(task_name: str) -> dict | None:
    """Retrieves the status for a named task."""
    status = _task_statuses.get(task_name)
    if status:
        # Auto-expire tasks older than 10 minutes that are 'completed'
        if status.get("done") and (time.time() - status["updated_at"] > 600):
            del _task_statuses[task_name]
            return None
    return status


def set_analyzing(post_id: str, msg: str = "正在分析视频...") -> None:
    """Mark a post as currently being analyzed."""
    _analyzing_posts[post_id] = {"msg": msg, "started_at": time.time()}


def clear_analyzing(post_id: str) -> None:
    """Remove a post from the analyzing set."""
    _analyzing_posts.pop(post_id, None)


def get_analyzing_posts() -> dict[str, dict]:
    """Return all posts currently being analyzed."""
    # Auto-expire entries older than 10 minutes (likely stale)
    now = time.time()
    expired = [pid for pid, info in _analyzing_posts.items() if now - info["started_at"] > 600]
    for pid in expired:
        del _analyzing_posts[pid]
    return dict(_analyzing_posts)
