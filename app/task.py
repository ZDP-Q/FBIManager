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

# Task type constants
TYPE_SYNC = "sync"
TYPE_ANALYSIS = "analysis"
TYPE_MONITOR = "monitor"

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
    """Return current UTC time as a string compatible with SQLite datetime() comparisons."""
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def create_task(task_id: str, name: str, task_type: str = "") -> dict[str, Any]:
    """Create a new task record. If a terminal task with the same ID exists, reset it."""
    now = _now()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO tasks (id, name, task_type, status, progress, message, error, result, started_at, updated_at, created_at)
               VALUES (?, ?, ?, ?, 0, '', '', '{}', ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 name = excluded.name,
                 task_type = excluded.task_type,
                 status = excluded.status,
                 progress = 0,
                 message = '',
                 error = '',
                 result = '{}',
                 started_at = excluded.started_at,
                 ended_at = NULL,
                 updated_at = excluded.updated_at,
                 created_at = excluded.created_at""",
            (task_id, name, task_type, STATUS_PENDING, now, now, now),
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
            sets.append("started_at = COALESCE(started_at, ?)")
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


_STALE_THRESHOLD_SECONDS = 600  # 10 minutes


def heartbeat_task(task_id: str) -> None:
    """Refresh a task's updated_at without changing any other fields. Call from active workers.
    Skips tasks in terminal states to avoid preventing cleanup."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE tasks SET updated_at = ? WHERE id = ? AND status NOT IN (?, ?, ?)",
            (_now(), task_id, STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELED),
        )


def _auto_fail_if_stale(task_id: str, task_dict: dict[str, Any]) -> None:
    """If a running task hasn't been updated in >10 min, mark it as failed in-place."""
    updated_at = task_dict.get("updated_at", "")
    if not updated_at:
        return
    try:
        last_update = datetime.fromisoformat(updated_at)
        if last_update.tzinfo is None:
            last_update = last_update.replace(tzinfo=UTC)
        elapsed = (datetime.now(UTC) - last_update).total_seconds()
        if elapsed > _STALE_THRESHOLD_SECONDS:
            logger.warning("[task] Task %s stale (%ds without update), auto-failing", task_id, int(elapsed))
            update_task(task_id, status=STATUS_FAILED, error=f"任务超时：{int(elapsed)} 秒无响应", message="任务超时已自动终止")
            task_dict["status"] = STATUS_FAILED
            task_dict["error"] = f"任务超时：{int(elapsed)} 秒无响应"
            task_dict["message"] = "任务超时已自动终止"
    except Exception as exc:
        logger.warning("[task] Failed to auto-fail stale task %s: %s", task_id, exc)


def get_task(task_id: str) -> dict[str, Any] | None:
    """Get task by ID. Returns None if not found. Auto-detects stale running tasks."""
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
    # Auto-detect stale running tasks (no update for 10+ minutes)
    if d["status"] == STATUS_RUNNING:
        _auto_fail_if_stale(task_id, d)
        # Re-read after potential update
        if d["status"] == STATUS_FAILED:
            return d
    return d


def cancel_task(task_id: str) -> bool:
    """Request cancellation of a running task. Returns True if task was running."""
    task = get_task(task_id)
    if not task or task["status"] != STATUS_RUNNING:
        return False
    update_task(task_id, status=STATUS_CANCELED, message="用户手动取消")
    return True


def is_task_running(task_id: str) -> bool:
    """Check if a task is currently running. get_task auto-fails stale tasks."""
    task = get_task(task_id)
    return task is not None and task["status"] == STATUS_RUNNING


async def create_task_if_not_running(task_id: str, name: str, task_type: str = "") -> bool:
    """Atomically check if task is running and create it if not. Returns True if created."""
    lock = await _get_task_lock(task_id)
    async with lock:
        if is_task_running(task_id):
            return False
        create_task(task_id, name, task_type)
        update_task(task_id, status=STATUS_RUNNING)
        try:
            await _cleanup_stale_locks_internal()
        except Exception:
            logger.warning("[task] Stale lock cleanup failed for %s (non-fatal)", task_id)
        return True


async def _cleanup_stale_locks_internal() -> None:
    """Remove locks for tasks that are no longer running. Caller must not hold _global_lock."""
    async with _global_lock:
        stale = [tid for tid, lock in _task_locks.items()
                 if not lock.locked() and not is_task_running(tid)]
        for tid in stale:
            _task_locks.pop(tid, None)


def cleanup_tasks(older_than_hours: int = 24) -> int:
    """Delete completed tasks older than the given hours. Returns count deleted.
    Clamped to 1-8760 hours (1 year) to prevent accidental mass deletion."""
    older_than_hours = max(1, min(older_than_hours, 8760))
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM tasks WHERE status IN (?, ?, ?) AND updated_at < datetime('now', ?)",
            (STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELED, f"-{older_than_hours} hours"),
        )
        return cursor.rowcount


@asynccontextmanager
async def task_runner(task_id: str, name: str, task_type: str = ""):
    """Async context manager that manages task lifecycle.

    Usage:
        async with task_runner("post_sync", "帖子同步", task_type="sync"):
            # do work, call update_task(task_id, progress=X, message=Y) periodically
        # On normal exit: status → success
        # On exception: status → failed, error set
    """
    created = await create_task_if_not_running(task_id, name, task_type)
    try:
        yield
        if created:
            update_task(task_id, status=STATUS_SUCCESS, progress=100)
    except asyncio.CancelledError:
        if created:
            update_task(task_id, status=STATUS_CANCELED, message="任务被取消")
        raise
    except Exception as exc:
        if created:
            update_task(task_id, status=STATUS_FAILED, error=str(exc)[:500])
        raise
    except BaseException as exc:
        if created:
            update_task(task_id, status=STATUS_FAILED, error=f"{type(exc).__name__}: {str(exc)[:400]}")
        raise


def list_tasks(
    task_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List tasks with optional type/status filters, ordered by created_at DESC."""
    conditions: list[str] = []
    params: list[Any] = []
    if task_type:
        conditions.append("task_type = ?")
        params.append(task_type)
    if status:
        conditions.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    # Clamp limit and offset to safe ranges
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    params.extend([limit, offset])
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        try:
            d["result"] = json.loads(d.get("result", "{}"))
        except (json.JSONDecodeError, TypeError):
            pass
        # Auto-detect stale running tasks (same as get_task)
        if d["status"] == STATUS_RUNNING:
            _auto_fail_if_stale(d["id"], d)
        result.append(d)
    return result


def count_tasks(task_type: str | None = None, status: str | None = None) -> int:
    """Count tasks with optional type/status filters."""
    conditions: list[str] = []
    params: list[Any] = []
    if task_type:
        conditions.append("task_type = ?")
        params.append(task_type)
    if status:
        conditions.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with get_connection() as conn:
        row = conn.execute(f"SELECT COUNT(*) FROM tasks {where}", params).fetchone()
    return row[0] if row else 0


def get_task_summary() -> dict[str, dict[str, int]]:
    """Get task counts grouped by type × status for overview cards.

    Returns:
        {
            "sync": {"running": 1, "success": 10, "failed": 2, "canceled": 0, "pending": 0},
            "analysis": {"running": 0, "success": 5, "failed": 1, "canceled": 0, "pending": 0},
            "monitor": {"running": 2, "success": 20, "failed": 3, "canceled": 0, "pending": 0},
            "other": {"running": 0, "success": 0, "failed": 0, "canceled": 0, "pending": 0},
        }
    """
    known_types = [TYPE_SYNC, TYPE_ANALYSIS, TYPE_MONITOR]
    statuses = [STATUS_RUNNING, STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELED, STATUS_PENDING]
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT task_type, status, COUNT(*) as cnt FROM tasks GROUP BY task_type, status"
        ).fetchall()
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        tt = row["task_type"] or ""
        st = row["status"]
        cnt = row["cnt"]
        if tt not in counts:
            counts[tt] = {}
        counts[tt][st] = cnt
    summary: dict[str, dict[str, int]] = {}
    for t in known_types:
        summary[t] = {}
        for s in statuses:
            summary[t][s] = counts.get(t, {}).get(s, 0)
    # Aggregate unknown/empty task_type into "other"
    other_counts: dict[str, int] = {}
    for tt, st_counts in counts.items():
        if tt not in known_types:
            for st, cnt in st_counts.items():
                other_counts[st] = other_counts.get(st, 0) + cnt
    summary["other"] = {}
    for s in statuses:
        summary["other"][s] = other_counts.get(s, 0)
    return summary
