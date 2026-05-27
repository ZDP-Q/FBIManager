"""Holds application-wide service singletons to avoid circular imports."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.monitor import MonitorService

from app.task import (
    STATUS_CANCELED,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_SUCCESS,
    get_task,
    is_task_running,
    update_task,
)

_monitor_service: "MonitorService | None" = None


def set_monitor_service(svc: "MonitorService") -> None:
    global _monitor_service
    _monitor_service = svc


def get_monitor_service() -> "MonitorService":
    if _monitor_service is None:
        raise RuntimeError("MonitorService not initialized")
    return _monitor_service


def update_task_status(task_name: str, status: dict) -> None:
    """Legacy API: translates old-style status dicts to the unified task service.

    Field mapping:
      - msg → message
      - percent → progress
      - done (bool) + error (bool) → status string
      - extra fields → result dict
    """
    kwargs: dict = {}

    if "msg" in status:
        kwargs["message"] = status["msg"]
    if "percent" in status:
        kwargs["progress"] = status["percent"]

    # Determine status from done/error flags
    if status.get("done"):
        if status.get("error"):
            kwargs["status"] = STATUS_FAILED
            if "msg" in status:
                kwargs["error"] = status["msg"]
        elif status.get("cancel"):
            kwargs["status"] = STATUS_CANCELED
        else:
            kwargs["status"] = STATUS_SUCCESS
    elif status.get("cancel"):
        kwargs["status"] = STATUS_CANCELED
    else:
        kwargs["status"] = STATUS_RUNNING

    # Collect extra fields into result
    _KNOWN = {"msg", "percent", "done", "error", "cancel", "updated_at"}
    extras = {k: v for k, v in status.items() if k not in _KNOWN}
    if extras:
        kwargs["result"] = extras

    # Use task_runner-style create if task doesn't exist yet
    if get_task(task_name) is None:
        from app.task import create_task
        create_task(task_name, task_name)

    update_task(task_name, **kwargs)


def get_task_status(task_name: str) -> dict | None:
    """Legacy API: returns status in old dict format for backward compatibility."""
    task = get_task(task_name)
    if task is None:
        return None

    # Translate to old format
    result: dict = {
        "msg": task.get("message", ""),
        "percent": task.get("progress", 0),
        "done": task["status"] in (STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELED),
        "error": task["status"] == STATUS_FAILED,
        "updated_at": task.get("updated_at", ""),
    }

    # Merge result extras
    task_result = task.get("result", {})
    if isinstance(task_result, dict):
        result.update(task_result)

    return result
