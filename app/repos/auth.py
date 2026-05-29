"""Admin authentication and session management repository."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.database import get_connection
from app.security import now_utc, now_utc_sql, session_expiry_sql


def get_admin_auth() -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, username, password_hash, password_salt, password_iterations, force_password_change, updated_at
            FROM admin_auth WHERE id = 1
            """
        ).fetchone()
    return dict(row) if row else None


def update_admin_password(*, password_hash: str, password_salt: str, password_iterations: int) -> None:
    with get_connection() as connection:
        with connection:
            connection.execute(
                """
                UPDATE admin_auth SET password_hash = ?, password_salt = ?, password_iterations = ?,
                    force_password_change = 0, updated_at = CURRENT_TIMESTAMP
                WHERE id = 1
                """,
                (password_hash, password_salt, password_iterations),
            )


def create_admin_session(*, session_id: str, ip: str, user_agent: str) -> None:
    with get_connection() as connection:
        with connection:
            connection.execute(
                "INSERT OR REPLACE INTO admin_sessions (session_id, expires_at, ip, user_agent) VALUES (?, ?, ?, ?)",
                (session_id, session_expiry_sql(), ip[:120], user_agent[:512]),
            )


def get_admin_session(session_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT session_id, expires_at, created_at, last_seen_at, ip, user_agent FROM admin_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    if not row:
        return None
    session = dict(row)
    expires_at = str(session.get("expires_at", ""))
    if not expires_at:
        return None
    if now_utc_sql() >= expires_at:
        delete_admin_session(session_id)
        return None
    return session


def touch_admin_session(session_id: str) -> None:
    with get_connection() as connection:
        with connection:
            connection.execute(
                "UPDATE admin_sessions SET expires_at = ?, last_seen_at = CURRENT_TIMESTAMP WHERE session_id = ?",
                (session_expiry_sql(), session_id),
            )


def delete_admin_session(session_id: str) -> None:
    with get_connection() as connection:
        with connection:
            connection.execute("DELETE FROM admin_sessions WHERE session_id = ?", (session_id,))


def delete_all_admin_sessions() -> None:
    with get_connection() as connection:
        with connection:
            connection.execute("DELETE FROM admin_sessions")


def cleanup_expired_admin_sessions() -> None:
    with get_connection() as connection:
        with connection:
            connection.execute("DELETE FROM admin_sessions WHERE expires_at <= ?", (now_utc_sql(),))


def is_ip_locked(ip: str) -> bool:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT lock_until FROM admin_login_attempts WHERE ip = ?", (ip,)
        ).fetchone()
    if not row:
        return False
    lock_until = str(row["lock_until"] or "")
    return bool(lock_until and now_utc_sql() < lock_until)


def register_failed_login(ip: str) -> int:
    now = now_utc()
    now_sql = now_utc_sql()
    lock_window_minutes = 15
    max_attempts = 5

    with get_connection() as connection:
        with connection:
            row = connection.execute(
                "SELECT failed_count, first_failed_at, lock_until FROM admin_login_attempts WHERE ip = ?",
                (ip,),
            ).fetchone()

            if row:
                lock_until = str(row["lock_until"] or "")
                if lock_until and now_sql < lock_until:
                    return int(row["failed_count"] or max_attempts)

                first_failed_at = str(row["first_failed_at"] or "")
                failed_count = int(row["failed_count"] or 0)

                if not first_failed_at:
                    failed_count = 1
                    first_failed_at = now_sql
                else:
                    try:
                        first_failed_dt = datetime.strptime(first_failed_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
                        age_min = (now - first_failed_dt).total_seconds() / 60
                    except Exception:
                        age_min = lock_window_minutes + 1
                    if age_min > lock_window_minutes:
                        failed_count = 1
                        first_failed_at = now_sql
                    else:
                        failed_count += 1

                new_lock_until = ""
                if failed_count >= max_attempts:
                    new_lock_until = (now + timedelta(minutes=lock_window_minutes)).strftime("%Y-%m-%d %H:%M:%S")

                connection.execute(
                    """
                    UPDATE admin_login_attempts
                    SET failed_count = ?, first_failed_at = ?, lock_until = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE ip = ?
                    """,
                    (failed_count, first_failed_at, new_lock_until or None, ip),
                )
                return failed_count

            connection.execute(
                """
                INSERT OR REPLACE INTO admin_login_attempts (ip, failed_count, first_failed_at, lock_until, updated_at)
                VALUES (?, 1, ?, NULL, CURRENT_TIMESTAMP)
                """,
                (ip, now_sql),
            )
            return 1


def clear_login_attempts(ip: str) -> None:
    with get_connection() as connection:
        with connection:
            connection.execute("DELETE FROM admin_login_attempts WHERE ip = ?", (ip,))
