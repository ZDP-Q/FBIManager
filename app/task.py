"""Unified task management service.

Persists task state to SQLite so progress survives server restarts.
All long-running operations (sync, chat sync, video analysis) should use this module.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from app.database import get_connection

logger = logging.getLogger("uvicorn.error")

# Status constants
STATUS_PENDING = "pending"

# Lock for atomic check-and-create operations (prevents TOCTOU race conditions)
_task_locks: dict[str, asyncio.Lock] = {}
_global_lock = asyncio.Lock()


async def _get_task_lock(task_id: str) -> asyncio.Lock:
    """Get or create a per-task lock for atomic operations."""
    async with _global_lock:
        if task_id not in _task_locks:
            _task_locks[task_id] = asyncio.Lock()
        return _task_locks[task_id]
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_CANCELED = "canceled"

_TERMINAL_STATUSES = {STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELED}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def create_task(task_id: str, name: str) -> dict[str, Any]:
    """Create a new task record. If a terminal task with the same ID exists, reset it."""
    now = _now()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO tasks (id, name, status, progress, message, error, result, started_at, updated_at, created_at)
               VALUES (?, ?, ?, 0, '', '', '{}', ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 name = excluded.name,
                 status = excluded.status,
                 progress = 0,
                 message = '',
                 error = '',
                 result = '{}',
                 started_at = excluded.started_at,
                 ended_at = NULL,
                 updated_at = excluded.updated_at,
                 created_at = excluded.created_at""",
            (task_id, name, STATUS_PENDING, now, now, now),
        )
    return get_task(task_id)


def update_task(
    task_id: str,
    *,
    status: str | None = None,
    progress: int | None = None,
    message: str | None = None,
    error: str | None = None,
    result: dict | str | None = None,
) -> dict[str, Any]:
    """Update task fields (partial update — only non-None fields are written)."""
    sets: list[str] = ["updated_at = ?"]
    params: list[Any] = [_now()]

    if status is not None:
        sets.append("status = ?")
        params.append(status)
        if status == STATUS_RUNNING:
            sets.append("started_at = ?")
            params.append(_now())
        if status in _TERMINAL_STATUSES:
            sets.append("ended_at = ?")
            params.append(_now())
    if progress is not None:
        sets.append("progress = ?")
        params.append(progress)
    if message is not None:
        sets.append("message = ?")
        params.append(message)
    if error is not None:
        sets.append("error = ?")
        params.append(error)
    if result is not None:
        sets.append("result = ?")
        params.append(json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else result)

    params.append(task_id)
    with get_connection() as conn:
        conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)
    return get_task(task_id)


def get_task(task_id: str) -> dict[str, Any] | None:
    """Get task by ID. Returns None if not found."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    # Parse result JSON
    try:
        d["result"] = json.loads(d.get("result", "{}"))
    except (json.JSONDecodeError, TypeError):
        pass
    return d


def cancel_task(task_id: str) -> bool:
    """Request cancellation of a running task. Returns True if task was running."""
    task = get_task(task_id)
    if not task or task["status"] != STATUS_RUNNING:
        return False
    update_task(task_id, status=STATUS_CANCELED, message="用户手动取消")
    return True


def is_task_running(task_id: str) -> bool:
    """Check if a task is currently running."""
    task = get_task(task_id)
    return task is not None and task["status"] == STATUS_RUNNING


async def create_task_if_not_running(task_id: str, name: str) -> bool:
    """Atomically check if task is running and create it if not. Returns True if created."""
    lock = await _get_task_lock(task_id)
    async with lock:
        if is_task_running(task_id):
            return False
        create_task(task_id, name)
        update_task(task_id, status=STATUS_RUNNING)
        return True


def cleanup_tasks(older_than_hours: int = 24) -> int:
    """Delete completed tasks older than the given hours. Returns count deleted."""
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM tasks WHERE status IN (?, ?, ?) AND updated_at < datetime('now', ?)",
            (STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELED, f"-{older_than_hours} hours"),
        )
        return cursor.rowcount


@asynccontextmanager
async def task_runner(task_id: str, name: str):
    """Async context manager that manages task lifecycle.

    Usage:
        async with task_runner("post_sync", "帖子同步"):
            # do work, call update_task(task_id, progress=X, message=Y) periodically
        # On normal exit: status → success
        # On exception: status → failed, error set
    """
    create_task(task_id, name)
    update_task(task_id, status=STATUS_RUNNING)
    try:
        yield
        update_task(task_id, status=STATUS_SUCCESS, progress=100)
    except Exception as exc:
        update_task(task_id, status=STATUS_FAILED, error=str(exc)[:500])
        raise
