"""Post and page profile repository."""
from __future__ import annotations

import json
from typing import Any

from app.database import get_connection


def upsert_page_profile(profile: dict[str, Any]) -> None:
    picture_url = profile.get("picture", {}).get("data", {}).get("url", "")
    with get_connection() as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO page_profiles (page_id, name, username, link, picture_url, fan_count, category, raw_json, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(page_id) DO UPDATE SET
                    name = excluded.name,
                    username = excluded.username,
                    link = excluded.link,
                    picture_url = excluded.picture_url,
                    fan_count = excluded.fan_count,
                    category = excluded.category,
                    raw_json = excluded.raw_json,
                    synced_at = CURRENT_TIMESTAMP
                """,
                (
                    profile.get("id", ""),
                    profile.get("name", ""),
                    profile.get("username", ""),
                    profile.get("link", ""),
                    picture_url,
                    profile.get("fan_count", 0),
                    profile.get("category", ""),
                    json.dumps(profile, ensure_ascii=False),
                ),
            )


def get_page_profile(page_id: str | None = None) -> dict[str, Any] | None:
    with get_connection() as connection:
        if page_id:
            row = connection.execute(
                """
                SELECT page_id, name, username, link, picture_url, fan_count, category, synced_at
                FROM page_profiles
                WHERE page_id = ? OR username = ?
                ORDER BY synced_at DESC
                LIMIT 1
                """,
                (page_id, page_id),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT page_id, name, username, link, picture_url, fan_count, category, synced_at FROM page_profiles ORDER BY synced_at DESC LIMIT 1"
            ).fetchone()
    return dict(row) if row else None


def get_canonical_page_id(page_id: str) -> str:
    """Resolve a page_id (could be numeric or username) to its canonical numeric ID."""
    profile = get_page_profile(page_id)
    if profile:
        return profile["page_id"]
    return page_id


def upsert_post(page_id: str, post: dict[str, Any]) -> None:
    with get_connection() as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO posts (id, page_id, message, created_time, full_picture, permalink_url, type, raw_json, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    page_id = excluded.page_id,
                    message = excluded.message,
                    created_time = excluded.created_time,
                    full_picture = excluded.full_picture,
                    permalink_url = excluded.permalink_url,
                    type = excluded.type,
                    raw_json = excluded.raw_json,
                    synced_at = CURRENT_TIMESTAMP
                """,
                (
                    post["id"],
                    page_id,
                    post.get("message", ""),
                    post.get("created_time", ""),
                    post.get("full_picture", ""),
                    post.get("permalink_url", ""),
                    post.get("type", ""),
                    json.dumps(post, ensure_ascii=False),
                ),
            )


def list_posts(page_id: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    with get_connection() as connection:
        query = """
            SELECT p.id, p.page_id, p.message, p.created_time, p.full_picture,
                   p.permalink_url, p.type, p.raw_json, p.synced_at,
                   (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) as local_comment_count
            FROM posts p
        """
        params = []
        if page_id:
            query += " WHERE p.page_id = ?"
            params.append(page_id)
        query += " ORDER BY p.created_time DESC, p.id DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = connection.execute(query, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def delete_posts(post_ids: list[str]) -> None:
    if not post_ids:
        return
    placeholders = ",".join("?" for _ in post_ids)
    with get_connection() as connection:
        with connection:
            connection.execute(f"DELETE FROM posts WHERE id IN ({placeholders})", post_ids)


def clear_page_posts(page_id: str) -> None:
    with get_connection() as connection:
        with connection:
            connection.execute("DELETE FROM posts WHERE page_id = ?", (page_id,))


def get_post(post_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, page_id, message, created_time, full_picture, permalink_url, type, raw_json, synced_at,
                   (SELECT COUNT(*) FROM comments c WHERE c.post_id = posts.id) as local_comment_count
            FROM posts WHERE id = ?
            """,
            (post_id,),
        ).fetchone()
    return dict(row) if row else None
