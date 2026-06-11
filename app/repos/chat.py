"""Conversation and chat statistics repository."""
from __future__ import annotations

import json
import logging
from typing import Any

from app.database import get_connection

logger = logging.getLogger("uvicorn.error")


def _extract_other_user(row, page_id: str) -> dict[str, str]:
    """Extract the non-page participant from a conversation row's participants_json."""
    participants = []
    try:
        p_data = json.loads(row["participants_json"] or "{}")
        participants = p_data.get("data") or []
    except Exception as exc:
        row_dict = dict(row) if not isinstance(row, dict) else row
        logger.warning("[repos] Failed to parse participants_json for conv %s: %s", row_dict.get("conversation_id", row_dict.get("id", "?")), exc)

    user_name = "未知用户"
    user_id = ""
    avatar_url = ""
    for p in participants:
        pid = p.get("id")
        if pid is not None and str(pid) != page_id:
            user_name = p.get("name", user_name)
            user_id = str(pid)
            avatar_url = (p.get("picture") or {}).get("data", {}).get("url", "")
            break
    return {"name": user_name, "user_id": user_id, "avatar_url": avatar_url}


def upsert_page_conversation(
    conv_id: str,
    page_id: str,
    updated_time: str,
    unread_count: int,
    participants_json: str,
) -> None:
    with get_connection() as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO page_conversations (id, page_id, updated_time, unread_count, participants_json, synced_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    updated_time = excluded.updated_time,
                    unread_count = excluded.unread_count,
                    participants_json = excluded.participants_json,
                    synced_at = CASE
                        WHEN page_conversations.updated_time != excluded.updated_time
                        THEN CURRENT_TIMESTAMP
                        ELSE page_conversations.synced_at
                    END
                """,
                (conv_id, page_id, updated_time, unread_count, participants_json),
            )


def upsert_conversation_message(
    msg_id: str,
    conv_id: str,
    message_text: str | None,
    sender_id: str | None,
    sender_name: str | None,
    created_time: str,
) -> None:
    with get_connection() as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO conversation_messages (id, conversation_id, message_text, sender_id, sender_name, created_time, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    message_text = excluded.message_text,
                    sender_id = excluded.sender_id,
                    sender_name = excluded.sender_name,
                    created_time = excluded.created_time,
                    synced_at = CURRENT_TIMESTAMP
                """,
                (msg_id, conv_id, message_text, sender_id, sender_name, created_time),
            )


def bulk_upsert_conversation_messages(messages: list[tuple]) -> None:
    """Batch insert messages for performance."""
    if not messages:
        return
    with get_connection() as connection:
        with connection:
            connection.executemany(
                """
                INSERT INTO conversation_messages (id, conversation_id, message_text, sender_id, sender_name, created_time, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    message_text = excluded.message_text,
                    sender_id = excluded.sender_id,
                    sender_name = excluded.sender_name,
                    created_time = excluded.created_time,
                    synced_at = CURRENT_TIMESTAMP
                """,
                messages,
            )


def get_latest_conversation_update(page_id: str) -> str | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT MAX(updated_time) as last_update FROM page_conversations WHERE page_id = ?", (page_id,)
        ).fetchone()
        return row["last_update"] if row else None


def get_last_sync_time(page_id: str) -> str | None:
    """Return the MAX(synced_at) for this page — the wall-clock time of the last sync."""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT MAX(synced_at) as last_sync FROM page_conversations WHERE page_id = ?",
            (page_id,),
        ).fetchone()
        return row["last_sync"] if row else None


def get_latest_message_time(conversation_id: str) -> str | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT MAX(created_time) as last_time FROM conversation_messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        return row["last_time"] if row else None


def get_conversation_updated_time(conv_id: str) -> str | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT updated_time FROM page_conversations WHERE id = ?", (conv_id,)
        ).fetchone()
        return row["updated_time"] if row else None


def check_message_exists(msg_id: str) -> bool:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM conversation_messages WHERE id = ?", (msg_id,)
        ).fetchone()
        return bool(row)


def get_chat_dashboard_stats(page_id: str) -> dict[str, Any]:
    with get_connection() as connection:
        total_users = connection.execute(
            "SELECT COUNT(*) FROM page_conversations WHERE page_id = ?", (page_id,)
        ).fetchone()[0]

        total_messages = connection.execute(
            """
            SELECT COUNT(*) FROM conversation_messages m
            JOIN page_conversations c ON m.conversation_id = c.id
            WHERE c.page_id = ?
            """,
            (page_id,),
        ).fetchone()[0]

        longest_chat = connection.execute(
            """
            SELECT COUNT(*) as msg_count, conversation_id FROM conversation_messages m
            JOIN page_conversations c ON m.conversation_id = c.id
            WHERE c.page_id = ? GROUP BY conversation_id ORDER BY msg_count DESC LIMIT 1
            """,
            (page_id,),
        ).fetchone()
        longest_msg_count = longest_chat["msg_count"] if (longest_chat and longest_chat["msg_count"] is not None) else 0

        longest_duration_row = connection.execute(
            """
            SELECT (julianday(max(substr(created_time, 1, 19))) - julianday(min(substr(created_time, 1, 19)))) as duration_days
            FROM conversation_messages m
            JOIN page_conversations c ON m.conversation_id = c.id
            WHERE c.page_id = ? AND created_time IS NOT NULL
            GROUP BY conversation_id ORDER BY duration_days DESC LIMIT 1
            """,
            (page_id,),
        ).fetchone()
        duration_raw = longest_duration_row["duration_days"] if longest_duration_row else None
        longest_duration_days = round(float(duration_raw), 1) if duration_raw is not None else 0

        streak_row = connection.execute(
            """
            WITH dates AS (
                SELECT DISTINCT date(substr(created_time, 1, 10)) as d, conversation_id
                FROM conversation_messages m
                JOIN page_conversations c ON m.conversation_id = c.id
                WHERE c.page_id = ? AND created_time IS NOT NULL
            ),
            groups AS (
                SELECT d, conversation_id,
                       julianday(d) - ROW_NUMBER() OVER (PARTITION BY conversation_id ORDER BY d) as grp
                FROM dates
            )
            SELECT COUNT(*) as streak_length FROM groups GROUP BY conversation_id, grp
            ORDER BY streak_length DESC LIMIT 1
            """,
            (page_id,),
        ).fetchone()
        max_streak = streak_row["streak_length"] if (streak_row and streak_row["streak_length"] is not None) else 0

        # Active user counts (based on message activity, excluding page's own messages)
        # Combined into a single query with conditional aggregation for efficiency.
        # Normalize created_time: strip timezone suffix and replace 'T' separator with ' '
        # so that SQLite string comparison with datetime('now') works correctly.
        # NOTE: Assumes Facebook timestamps are always UTC (+0000). If non-UTC offsets are
        # ever stored, this comparison will be incorrect — normalize at write time instead.
        active_row = connection.execute(
            """
            SELECT
                COUNT(DISTINCT CASE WHEN REPLACE(SUBSTR(m.created_time, 1, 19), 'T', ' ') > datetime('now', '-1 day') THEN m.conversation_id END) as active_24h,
                COUNT(DISTINCT CASE WHEN REPLACE(SUBSTR(m.created_time, 1, 19), 'T', ' ') > datetime('now', '-7 days') THEN m.conversation_id END) as active_7d,
                COUNT(DISTINCT CASE WHEN REPLACE(SUBSTR(m.created_time, 1, 19), 'T', ' ') > datetime('now', '-15 days') THEN m.conversation_id END) as active_15d
            FROM conversation_messages m
            JOIN page_conversations c ON m.conversation_id = c.id
            WHERE c.page_id = ? AND m.sender_id != c.page_id
            """,
            (page_id,),
        ).fetchone()
        active_24h = active_row[0] if active_row else 0
        active_7d = active_row[1] if active_row else 0
        active_15d = active_row[2] if active_row else 0

    return {
        "total_users": total_users,
        "total_messages": total_messages,
        "longest_msg_count": longest_msg_count,
        "longest_duration_days": longest_duration_days,
        "max_streak": max_streak,
        "active_24h": active_24h,
        "active_7d": active_7d,
        "active_15d": active_15d,
    }


def get_user_message_counts(page_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT c.id as conversation_id, participants_json, COUNT(m.id) as msg_count
            FROM page_conversations c
            LEFT JOIN conversation_messages m ON c.id = m.conversation_id
            WHERE c.page_id = ?
            GROUP BY c.id ORDER BY msg_count ASC LIMIT ?
            """,
            (page_id, limit),
        ).fetchall()

    results = []
    for row in rows:
        user = _extract_other_user(row, page_id)
        results.append({"name": user["name"], "value": row["msg_count"]})
    return results


def get_chat_detailed_stats(page_id: str) -> dict[str, Any]:
    """Calculate Max, Min, Median, Average for messages and streaks per user."""
    with get_connection() as connection:
        msg_counts = connection.execute(
            """
            SELECT COUNT(m.id) as cnt
            FROM page_conversations c
            LEFT JOIN conversation_messages m ON c.id = m.conversation_id AND m.sender_id != c.page_id
            WHERE c.page_id = ?
            GROUP BY c.id
            """,
            (page_id,),
        ).fetchall()

        counts = [row["cnt"] for row in msg_counts] if msg_counts else [0]
        counts.sort()

        def get_percentile(data: list[int], p: float) -> int:
            if not data:
                return 0
            idx = int(len(data) * p)
            return data[min(idx, len(data) - 1)]

        msg_stats = {
            "max": max(counts), "min": min(counts),
            "avg": round(sum(counts) / len(counts), 1) if counts else 0,
            "median": counts[len(counts) // 2] if counts else 0,
            "p99": get_percentile(counts, 0.99), "p95": get_percentile(counts, 0.95),
            "p90": get_percentile(counts, 0.90), "p80": get_percentile(counts, 0.80),
        }

        conv_msg_counts = connection.execute(
            """
            SELECT c.id, COUNT(m.id) as cnt
            FROM page_conversations c
            LEFT JOIN conversation_messages m ON c.id = m.conversation_id AND m.sender_id != c.page_id
            WHERE c.page_id = ?
            GROUP BY c.id
            """,
            (page_id,),
        ).fetchall()

        msg_map = {row["id"]: row["cnt"] for row in conv_msg_counts}
        all_msg_counts = sorted(msg_map.values())
        thresholds = {
            "p99": get_percentile(all_msg_counts, 0.99), "p95": get_percentile(all_msg_counts, 0.95),
            "p90": get_percentile(all_msg_counts, 0.90), "p80": get_percentile(all_msg_counts, 0.80),
            "all": 0,
        }

        streak_data_rows = connection.execute(
            """
            WITH dates AS (
                SELECT DISTINCT date(substr(m.created_time, 1, 10)) as d, conversation_id
                FROM conversation_messages m
                JOIN page_conversations c ON m.conversation_id = c.id
                WHERE c.page_id = ? AND m.created_time IS NOT NULL AND m.sender_id != c.page_id
            ),
            groups AS (
                SELECT d, conversation_id,
                       julianday(d) - ROW_NUMBER() OVER (PARTITION BY conversation_id ORDER BY d) as grp
                FROM dates
            )
            SELECT conversation_id, COUNT(*) as streak_len FROM groups GROUP BY conversation_id, grp
            """,
            (page_id,),
        ).fetchall()

        def calc_stats(data_list: list[int]) -> dict[str, Any]:
            if not data_list:
                return {"max": 0, "min": 0, "avg": 0, "median": 0}
            data_list.sort()
            return {
                "max": max(data_list), "min": min(data_list),
                "avg": round(sum(data_list) / len(data_list), 1),
                "median": data_list[len(data_list) // 2],
            }

        streak_stats = {}
        for label, threshold in thresholds.items():
            subset = [r["streak_len"] for r in streak_data_rows if msg_map.get(r["conversation_id"], 0) >= threshold]
            streak_stats[label] = calc_stats(subset)

        user_active_days_map = connection.execute(
            """
            SELECT c.id, COUNT(DISTINCT date(substr(m.created_time, 1, 10))) as days
            FROM page_conversations c
            LEFT JOIN conversation_messages m ON c.id = m.conversation_id AND m.sender_id != c.page_id
            WHERE c.page_id = ?
            GROUP BY c.id
            """,
            (page_id,),
        ).fetchall()

        user_active_days = sorted([r["days"] for r in user_active_days_map]) if user_active_days_map else [0]
        user_active_days_dict = {r["id"]: r["days"] for r in user_active_days_map}

        histograms = {}
        for label in ["all", "p99", "p95", "p90", "p80"]:
            threshold = thresholds[label]
            tier_days = [days for cid, days in user_active_days_dict.items() if msg_map.get(cid, 0) >= threshold]
            hist: dict[int, int] = {}
            if tier_days:
                for val in tier_days:
                    bin_val = int(val)
                    hist[bin_val] = hist.get(bin_val, 0) + 1
            max_day = max(hist.keys()) if hist else 0
            sorted_labels = []
            sorted_values = []
            if max_day > 0:
                for day in range(1, int(max_day) + 1):
                    sorted_labels.append(f"{day}天")
                    sorted_values.append(hist.get(day, 0))
            histograms[label] = {"labels": sorted_labels, "values": sorted_values}

        active_days_dist = {
            "max": max(user_active_days) if user_active_days else 0,
            "min": min(user_active_days) if user_active_days else 0,
            "avg": round(sum(user_active_days) / len(user_active_days), 1) if user_active_days else 0,
            "median": user_active_days[len(user_active_days) // 2] if user_active_days else 0,
            "p99": get_percentile(user_active_days, 0.99),
            "p95": get_percentile(user_active_days, 0.95),
            "p90": get_percentile(user_active_days, 0.90),
            "p80": get_percentile(user_active_days, 0.80),
        }

    return {
        "messages": msg_stats,
        "streaks": streak_stats,
        "active_days_dist": active_days_dist,
        "histograms": histograms,
        "all_msg_counts_sorted": counts[::-1],
    }


def get_user_ranking_stats(page_id: str, limit: int = 100) -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT c.id as conversation_id, c.participants_json,
                   COUNT(m.id) as total_message_count,
                   COUNT(DISTINCT date(substr(m.created_time, 1, 10))) as total_active_days,
                   MAX(m.created_time) as last_active_time
            FROM page_conversations c
            LEFT JOIN conversation_messages m ON c.id = m.conversation_id
            WHERE c.page_id = ?
            GROUP BY c.id ORDER BY total_message_count DESC LIMIT ?
            """,
            (page_id, limit),
        ).fetchall()

    results = []
    for row in rows:
        user = _extract_other_user(row, page_id)
        results.append({
            "name": user["name"], "user_id": user["user_id"], "avatar_url": user["avatar_url"],
            "message_count": row["total_message_count"], "active_days": row["total_active_days"],
            "last_active_time": row["last_active_time"] or "",
        })
    return results
