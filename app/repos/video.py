"""Video analysis repository."""
from __future__ import annotations

import json
from typing import Any

from app.database import get_connection


def save_video_analysis(post_id: str, title: str, content: str, post_time: int) -> int:
    with get_connection() as connection:
        with connection:
            cursor = connection.execute(
                "INSERT INTO video_analyses (post_id, title, content, post_time, created_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (post_id, title, content, post_time),
            )
            return cursor.lastrowid


def update_video_analysis(post_id: str, content: str) -> bool:
    with get_connection() as connection:
        with connection:
            row = connection.execute(
                "SELECT id FROM video_analyses WHERE post_id = ? ORDER BY created_at DESC LIMIT 1", (post_id,)
            ).fetchone()
            if not row:
                return False
            connection.execute("UPDATE video_analyses SET content = ? WHERE id = ?", (content, row["id"]))
            return True


def get_video_analysis(post_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM video_analyses WHERE post_id = ? ORDER BY created_at DESC LIMIT 1", (post_id,)
        ).fetchone()
    return dict(row) if row else None


def parse_video_analysis_content(content: str) -> dict[str, Any] | None:
    if not content:
        return None
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict) and all(k in parsed for k in ("location", "behavior", "environment")):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return None


def list_video_analyses(limit: int = 50) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM video_analyses ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def update_video_analysis_pushed(post_id: str, pushed_at: str) -> bool:
    with get_connection() as connection:
        with connection:
            row = connection.execute(
                "SELECT id FROM video_analyses WHERE post_id = ? ORDER BY created_at DESC LIMIT 1", (post_id,)
            ).fetchone()
            if not row:
                return False
            connection.execute("UPDATE video_analyses SET pushed_at = ? WHERE id = ?", (pushed_at, row["id"]))
            return True


def list_posts_with_analysis(page_id: str, limit: int = 200) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT p.id, p.message, p.created_time, p.type,
                   va.content AS analysis_content, va.post_time AS analysis_post_time,
                   va.pushed_at, va.id AS analysis_id
            FROM posts p
            LEFT JOIN video_analyses va ON va.id = (
                SELECT id FROM video_analyses WHERE post_id = p.id ORDER BY created_at DESC LIMIT 1
            )
            WHERE p.page_id = ?
            ORDER BY p.created_time DESC
            LIMIT ?
            """,
            (page_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]
