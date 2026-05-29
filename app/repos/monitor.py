"""Monitor management and auto-monitor config repository."""
from __future__ import annotations

from typing import Any

from app.database import get_connection


def create_monitor(post_id: str, interval_seconds: int = 1800, max_depth: int = 1) -> int:
    with get_connection() as connection:
        with connection:
            connection.execute(
                "INSERT OR IGNORE INTO post_monitors (post_id, enabled, interval_seconds, max_depth) VALUES (?, 1, ?, ?)",
                (post_id, interval_seconds, max_depth),
            )
            row = connection.execute("SELECT id FROM post_monitors WHERE post_id = ?", (post_id,)).fetchone()
            return row["id"]


def list_monitors(page_id: str | None = None) -> list[dict[str, Any]]:
    with get_connection() as connection:
        if page_id:
            rows = connection.execute(
                """
                SELECT m.id, m.post_id, m.enabled, m.interval_seconds, m.max_depth,
                       m.created_at, m.last_run_at, m.last_run_status,
                       p.page_id, p.message AS post_message, p.created_time AS post_created_time, p.permalink_url
                FROM post_monitors m LEFT JOIN posts p ON m.post_id = p.id
                WHERE p.page_id = ? ORDER BY m.created_at DESC
                """,
                (page_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT m.id, m.post_id, m.enabled, m.interval_seconds, m.max_depth,
                       m.created_at, m.last_run_at, m.last_run_status,
                       p.page_id, p.message AS post_message, p.created_time AS post_created_time, p.permalink_url
                FROM post_monitors m LEFT JOIN posts p ON m.post_id = p.id
                ORDER BY m.created_at DESC
                """
            ).fetchall()
    return [dict(row) for row in rows]


def get_monitor(monitor_id: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT m.id, m.post_id, m.enabled, m.interval_seconds, m.max_depth,
                   m.created_at, m.last_run_at, m.last_run_status,
                   p.page_id, p.message AS post_message, p.created_time AS post_created_time, p.permalink_url
            FROM post_monitors m LEFT JOIN posts p ON m.post_id = p.id
            WHERE m.id = ?
            """,
            (monitor_id,),
        ).fetchone()
    return dict(row) if row else None


def get_monitor_by_post(post_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM post_monitors WHERE post_id = ?", (post_id,)).fetchone()
    return dict(row) if row else None


def update_monitor(monitor_id: int, **kwargs: Any) -> None:
    allowed = {"enabled", "interval_seconds", "max_depth", "last_run_at", "last_run_status"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [monitor_id]
    with get_connection() as connection:
        with connection:
            connection.execute(f"UPDATE post_monitors SET {set_clause} WHERE id = ?", values)


def list_monitored_post_ids(page_id: str) -> set[str]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT m.post_id FROM post_monitors m LEFT JOIN posts p ON m.post_id = p.id WHERE p.page_id = ?",
            (page_id,),
        ).fetchall()
    return {row["post_id"] for row in rows}


def delete_monitor(monitor_id: int) -> None:
    with get_connection() as connection:
        with connection:
            connection.execute("DELETE FROM post_monitors WHERE id = ?", (monitor_id,))


def delete_monitors(monitor_ids: list[int]) -> None:
    if not monitor_ids:
        return
    placeholders = ",".join("?" for _ in monitor_ids)
    with get_connection() as connection:
        with connection:
            connection.execute(f"DELETE FROM post_monitors WHERE id IN ({placeholders})", monitor_ids)


# --- Auto-monitor configuration ---

def get_auto_monitor_config() -> dict[str, Any]:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM auto_monitor_configs WHERE id = 1").fetchone()
    if row:
        return dict(row)
    return {"enabled": 0, "max_posts": 10}


def update_auto_monitor_config(*, enabled: int | None = None, max_posts: int | None = None) -> None:
    fields = []
    params = []
    if enabled is not None:
        fields.append("enabled = ?")
        params.append(enabled)
    if max_posts is not None:
        fields.append("max_posts = ?")
        params.append(max_posts)
    if not fields:
        return
    params.append(1)
    with get_connection() as connection:
        with connection:
            connection.execute(
                f"UPDATE auto_monitor_configs SET {', '.join(fields)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                params,
            )


def list_auto_monitor_schedules() -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute("SELECT * FROM auto_monitor_schedules ORDER BY trigger_time ASC").fetchall()
    return [dict(row) for row in rows]


def add_auto_monitor_schedule(trigger_time: str) -> int:
    with get_connection() as connection:
        with connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO auto_monitor_schedules (trigger_time) VALUES (?)", (trigger_time,)
            )
            return int(cursor.lastrowid or 0)


def delete_auto_monitor_schedule(schedule_id: int) -> None:
    with get_connection() as connection:
        with connection:
            connection.execute("DELETE FROM auto_monitor_schedules WHERE id = ?", (schedule_id,))


def update_auto_monitor_schedule(schedule_id: int, *, enabled: int) -> None:
    with get_connection() as connection:
        with connection:
            connection.execute(
                "UPDATE auto_monitor_schedules SET enabled = ? WHERE id = ?", (enabled, schedule_id)
            )


def mark_auto_monitor_triggered(schedule_id: int, triggered_at: str) -> None:
    with get_connection() as connection:
        with connection:
            connection.execute(
                "UPDATE auto_monitor_schedules SET last_triggered_at = ? WHERE id = ?", (triggered_at, schedule_id)
            )
