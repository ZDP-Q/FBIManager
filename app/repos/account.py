"""Account configuration and model config repository."""
from __future__ import annotations

from typing import Any

from app.database import get_connection


def list_accounts() -> list[dict[str, Any]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, name, page_access_token, verify_token, page_id, api_version, is_active, created_at, updated_at
            FROM account_configs
            ORDER BY is_active DESC, id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_active_account() -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, name, page_access_token, verify_token, page_id, api_version, is_active, created_at, updated_at
            FROM account_configs
            WHERE is_active = 1
            ORDER BY id ASC
            LIMIT 1
            """
        ).fetchone()
    return dict(row) if row else None


def get_account_by_id(account_id: int) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, name, page_access_token, verify_token, page_id, api_version, is_active, created_at, updated_at
            FROM account_configs
            WHERE id = ?
            """,
            (account_id,),
        ).fetchone()
    return dict(row) if row else None


def get_account_by_page_id(page_id: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, name, page_access_token, verify_token, page_id, api_version, is_active, created_at, updated_at
            FROM account_configs
            WHERE page_id = ?
            LIMIT 1
            """,
            (page_id,),
        ).fetchone()
    return dict(row) if row else None


def get_account_by_verify_token(verify_token: str) -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, name, page_access_token, verify_token, page_id, api_version, is_active, created_at, updated_at
            FROM account_configs
            WHERE verify_token = ?
            LIMIT 1
            """,
            (verify_token,),
        ).fetchone()
    return dict(row) if row else None


def create_account(
    *,
    name: str,
    page_access_token: str,
    verify_token: str,
    page_id: str,
    api_version: str,
    is_active: int = 0,
) -> int:
    with get_connection() as connection:
        with connection:
            if is_active:
                connection.execute("UPDATE account_configs SET is_active = 0")
            cursor = connection.execute(
                """
                INSERT OR REPLACE INTO account_configs (
                    name, page_access_token, verify_token, page_id, api_version, is_active, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (name, page_access_token, verify_token, page_id, api_version, int(bool(is_active))),
            )
            return int(cursor.lastrowid)


def update_account(account_id: int, **kwargs: Any) -> None:
    allowed = {"name", "page_access_token", "verify_token", "page_id", "api_version", "is_active"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    with get_connection() as connection:
        with connection:
            if "is_active" in fields and int(bool(fields["is_active"])) == 1:
                connection.execute("UPDATE account_configs SET is_active = 0")
                fields["is_active"] = 1
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            values = list(fields.values()) + [account_id]
            connection.execute(
                f"UPDATE account_configs SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                values,
            )


def delete_account(account_id: int) -> None:
    with get_connection() as connection:
        with connection:
            connection.execute("DELETE FROM account_configs WHERE id = ?", (account_id,))
            active = connection.execute("SELECT id FROM account_configs WHERE is_active = 1 LIMIT 1").fetchone()
            if active is None:
                fallback = connection.execute("SELECT id FROM account_configs ORDER BY id ASC LIMIT 1").fetchone()
                if fallback is not None:
                    connection.execute("UPDATE account_configs SET is_active = 1 WHERE id = ?", (fallback["id"],))


def set_active_account(account_id: int) -> None:
    with get_connection() as connection:
        with connection:
            connection.execute("UPDATE account_configs SET is_active = 0")
            connection.execute(
                "UPDATE account_configs SET is_active = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (account_id,),
            )


def bulk_import_accounts(accounts: list[dict[str, Any]]) -> int:
    """Import multiple accounts, UPSERT by page_id."""
    count = 0
    with get_connection() as connection:
        with connection:
            for acc in accounts:
                page_id = str(acc.get("page_id", "")).strip()
                if not page_id:
                    continue
                connection.execute(
                    """
                    INSERT INTO account_configs (name, page_access_token, verify_token, page_id, api_version, is_active, updated_at)
                    VALUES (?, ?, ?, ?, ?, 0, CURRENT_TIMESTAMP)
                    ON CONFLICT(page_id) DO UPDATE SET
                        name = excluded.name,
                        page_access_token = excluded.page_access_token,
                        verify_token = excluded.verify_token,
                        api_version = excluded.api_version,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        acc.get("name", f"Imported {page_id}"),
                        acc.get("page_access_token", ""),
                        acc.get("verify_token", ""),
                        page_id,
                        acc.get("api_version", "v25.0") or "v25.0",
                    ),
                )
                count += 1
            if count > 0:
                connection.execute("DELETE FROM account_configs WHERE name = '默认账号' OR page_id = 'default-page'")
    return count


def get_model_config() -> dict[str, Any] | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, reply_api_base_url, reply_api_key, reply_model,
                   video_api_base_url, video_api_key, video_model,
                   prompt_template, app_secret, updated_at
            FROM model_configs
            WHERE id = 1
            """
        ).fetchone()
    return dict(row) if row else None


def upsert_model_config(
    *,
    reply_api_base_url: str = '',
    reply_api_key: str = '',
    reply_model: str = '',
    video_api_base_url: str = '',
    video_api_key: str = '',
    video_model: str = '',
    prompt_template: str = 'reply_prompt.j2',
    app_secret: str = '',
) -> None:
    with get_connection() as connection:
        with connection:
            connection.execute(
                """
                INSERT INTO model_configs (id, reply_api_base_url, reply_api_key, reply_model,
                    video_api_base_url, video_api_key, video_model,
                    prompt_template, app_secret, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(id) DO UPDATE SET
                    reply_api_base_url = excluded.reply_api_base_url,
                    reply_api_key = excluded.reply_api_key,
                    reply_model = excluded.reply_model,
                    video_api_base_url = excluded.video_api_base_url,
                    video_api_key = excluded.video_api_key,
                    video_model = excluded.video_model,
                    prompt_template = excluded.prompt_template,
                    app_secret = excluded.app_secret,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (reply_api_base_url, reply_api_key, reply_model,
                 video_api_base_url, video_api_key, video_model,
                 prompt_template, app_secret),
            )
