"""Comment management repository."""
from __future__ import annotations

import json
import logging
from typing import Any

from app.database import get_connection

logger = logging.getLogger("uvicorn.error")


def _extract_comment_message(comment: dict[str, Any]) -> str:
    """Extract display text from a comment, handling stickers and attachments."""
    msg = (comment.get("message") or "").strip()
    if msg:
        return msg
    story = (comment.get("story") or "").strip()
    if story:
        return f"[{story}]"
    attachment = comment.get("attachment") or {}
    if attachment:
        att_type = (attachment.get("type") or "").lower()
        media = attachment.get("media", {})
        title = (media.get("title") or "").strip() if media else ""
        if title:
            return f"[{att_type}: {title}]"
        desc = (attachment.get("description") or "").strip()
        if desc:
            return f"[{att_type}: {desc}]"
        return f"[{att_type}]"
    return ""


def _insert_comment(connection, post_id: str, parent_comment_id: str | None, comment: dict[str, Any]) -> None:
    author = comment.get("from") or {}
    message = _extract_comment_message(comment)
    if parent_comment_id is None:
        parent_data = comment.get("parent")
        if parent_data:
            parent_comment_id = parent_data.get("id")
    connection.execute(
        """
        INSERT INTO comments (id, post_id, parent_comment_id, message, author_name, author_id, created_time, raw_json, screened, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET
            message = excluded.message,
            author_name = excluded.author_name,
            author_id = excluded.author_id,
            raw_json = excluded.raw_json,
            synced_at = CURRENT_TIMESTAMP
        """,
        (
            comment["id"],
            post_id,
            parent_comment_id,
            message,
            author.get("name", "匿名用户"),
            author.get("id", ""),
            comment.get("created_time", ""),
            json.dumps(comment, ensure_ascii=False),
        ),
    )
    for reply in (comment.get("replies") or {}).get("data") or []:
        _insert_comment(connection, post_id, comment["id"], reply)


def replace_comments_for_post(post_id: str, comments: list[dict[str, Any]]) -> None:
    with get_connection() as connection:
        with connection:
            connection.execute("DELETE FROM comments WHERE post_id = ?", (post_id,))
            for comment in comments:
                _insert_comment(connection, post_id, None, comment)


def get_comment(comment_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT id, post_id, parent_comment_id, message, author_name, author_id, created_time, synced_at FROM comments WHERE id = ?",
            (comment_id,),
        ).fetchone()
    return dict(row) if row else None


def list_comments_by_post_ids(post_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not post_ids:
        return {}
    placeholders = ",".join("?" for _ in post_ids)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT id, post_id, parent_comment_id, message, author_name, author_id, created_time, synced_at
            FROM comments
            WHERE post_id IN ({placeholders})
            ORDER BY created_time ASC, id ASC
            """,
            post_ids,
        ).fetchall()

    comments_by_post: dict[str, list[dict[str, Any]]] = {pid: [] for pid in post_ids}
    comment_map: dict[str, dict[str, Any]] = {}

    comment_ids = [row["id"] for row in rows]
    ids_with_attachments: set[str] = set()
    if comment_ids:
        id_placeholders = ",".join("?" for _ in comment_ids)
        with get_connection() as conn:
            att_rows = conn.execute(
                f"SELECT DISTINCT comment_id FROM comment_attachments WHERE comment_id IN ({id_placeholders})",
                comment_ids,
            ).fetchall()
            ids_with_attachments = {r["comment_id"] for r in att_rows}

    for row in rows:
        item = dict(row)
        item["replies"] = []
        item["has_attachment"] = item["id"] in ids_with_attachments
        comment_map[item["id"]] = item

    for item in comment_map.values():
        parent_id = item["parent_comment_id"]
        if parent_id and parent_id in comment_map:
            comment_map[parent_id]["replies"].append(item)
        else:
            if item["post_id"] in comments_by_post:
                comments_by_post[item["post_id"]].append(item)
    return comments_by_post


def delete_comment_local(comment_id: str) -> None:
    with get_connection() as connection:
        with connection:
            connection.execute("DELETE FROM comments WHERE id = ?", (comment_id,))


def get_screened_comment_ids(post_id: str) -> set[str]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT id FROM comments WHERE post_id = ? AND screened = 1", (post_id,)
        ).fetchall()
    return {row["id"] for row in rows}


def mark_comments_screened(comment_ids: list[str]) -> None:
    if not comment_ids:
        return
    placeholders = ",".join("?" for _ in comment_ids)
    with get_connection() as connection:
        with connection:
            connection.execute(f"UPDATE comments SET screened = 1 WHERE id IN ({placeholders})", comment_ids)


def get_latest_comment_time(post_id: str) -> str | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT created_time FROM comments WHERE post_id = ? ORDER BY created_time DESC LIMIT 1", (post_id,)
        ).fetchone()
    return row["created_time"] if row else None


def count_pending_comments(post_id: str) -> int:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM comments WHERE post_id = ? AND screened = 0", (post_id,)
        ).fetchone()
    return row[0] if row else 0


def count_all_comments(post_id: str) -> int:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM comments WHERE post_id = ?", (post_id,)
        ).fetchone()
    return row[0] if row else 0


def list_unreplied_comments(post_id: str, exclude_author_id: str = "") -> list[dict[str, Any]]:
    """List comments that are not replied yet and not from the page itself."""
    query = """
        SELECT c.id, c.author_name, c.author_id, c.message
        FROM comments c
        LEFT JOIN replied_comments r ON c.id = r.comment_id
        WHERE c.post_id = ? AND r.comment_id IS NULL
    """
    params: list[Any] = [post_id]
    if exclude_author_id:
        query += " AND c.author_id != ?"
        params.append(exclude_author_id)
    query += " AND (c.message IS NOT NULL AND c.message != '') ORDER BY RANDOM()"
    with get_connection() as connection:
        rows = connection.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def get_oldest_pending_comment_time(post_id: str) -> str | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT created_time FROM comments WHERE post_id = ? AND screened = 0 ORDER BY created_time ASC LIMIT 1",
            (post_id,),
        ).fetchone()
    return row[0] if row else None


def list_pending_comments(post_id: str) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT id, author_name, author_id, message FROM comments WHERE post_id = ? AND screened = 0 ORDER BY created_time ASC",
            (post_id,),
        ).fetchall()
    return [dict(row) for row in rows]


# --- Comment attachments ---

def insert_comment_attachment(comment_id: str, media_type: str, media_url: str, data: bytes) -> None:
    with get_connection() as connection:
        with connection:
            connection.execute(
                "INSERT OR IGNORE INTO comment_attachments (comment_id, media_type, media_url, data) VALUES (?, ?, ?, ?)",
                (comment_id, media_type, media_url, data),
            )


def get_comment_attachments(comment_id: str) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT id, comment_id, media_type, media_url, data FROM comment_attachments WHERE comment_id = ?",
            (comment_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def has_attachment(comment_id: str) -> bool:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM comment_attachments WHERE comment_id = ? LIMIT 1", (comment_id,)
        ).fetchone()
    return row is not None


# --- Reply deduplication ---

def has_replied(comment_id: str) -> bool:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM replied_comments WHERE comment_id = ?", (comment_id,)
        ).fetchone()
    return row is not None


def count_replied_comments(post_id: str) -> int:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM replied_comments WHERE post_id = ?", (post_id,)
        ).fetchone()
    return row[0] if row else 0


def mark_replied(comment_id: str, post_id: str, monitor_id: int | None, reply_message: str) -> None:
    with get_connection() as connection:
        with connection:
            connection.execute(
                "INSERT OR IGNORE INTO replied_comments (comment_id, post_id, monitor_id, reply_message) VALUES (?, ?, ?, ?)",
                (comment_id, post_id, monitor_id, reply_message),
            )


def unmark_replied(comment_id: str) -> None:
    with get_connection() as connection:
        with connection:
            connection.execute("DELETE FROM replied_comments WHERE comment_id = ?", (comment_id,))


def list_replied_for_monitor(monitor_id: int, limit: int = 50) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT r.comment_id, r.post_id, r.monitor_id, r.reply_message, r.replied_at,
                   c.message AS comment_message, c.author_name
            FROM replied_comments r
            INNER JOIN comments c ON r.comment_id = c.id
            WHERE r.monitor_id = ?
            ORDER BY r.replied_at DESC
            LIMIT ?
            """,
            (monitor_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def list_replied_for_post(post_id: str, limit: int = 20) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT r.comment_id, r.post_id, r.reply_message, r.replied_at,
                   c.message AS comment_message, c.author_name
            FROM replied_comments r
            INNER JOIN comments c ON r.comment_id = c.id
            WHERE r.post_id = ?
            ORDER BY r.replied_at DESC
            LIMIT ?
            """,
            (post_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_comment(post_id: str, parent_comment_id: str | None, comment: dict[str, Any]) -> None:
    """Insert or update a single comment (used by monitor service for incremental updates)."""
    with get_connection() as connection:
        with connection:
            _insert_comment(connection, post_id, parent_comment_id, comment)
