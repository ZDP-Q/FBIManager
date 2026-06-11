from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.security import PBKDF2_ITERATIONS, generate_salt, hash_password, is_strong_password

DB_PATH = PROJECT_ROOT / "data" / "facebookmsg.sqlite3"
POSTS_JSON = PROJECT_ROOT / "posts_db.json"
COMMENTS_JSON = PROJECT_ROOT / "comments_db.json"

logger = logging.getLogger("uvicorn.error")

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS page_profiles (
    page_id TEXT PRIMARY KEY,
    name TEXT,
    username TEXT,
    link TEXT,
    picture_url TEXT,
    fan_count INTEGER,
    category TEXT,
    raw_json TEXT NOT NULL,
    synced_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL,
    message TEXT,
    created_time TEXT,
    full_picture TEXT,
    permalink_url TEXT,
    type TEXT NOT NULL DEFAULT '',
    is_hidden INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL,
    synced_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (page_id) REFERENCES page_profiles(page_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL,
    parent_comment_id TEXT,
    message TEXT,
    author_name TEXT,
    author_id TEXT,
    created_time TEXT,
    raw_json TEXT NOT NULL,
    synced_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
    FOREIGN KEY (parent_comment_id) REFERENCES comments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_posts_page_id ON posts(page_id);
CREATE INDEX IF NOT EXISTS idx_comments_post_id ON comments(post_id);
CREATE INDEX IF NOT EXISTS idx_comments_parent_id ON comments(parent_comment_id);

CREATE TABLE IF NOT EXISTS post_monitors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1,
    interval_seconds INTEGER NOT NULL DEFAULT 1800,
    max_depth INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_run_at TEXT,
    last_run_status TEXT,
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS replied_comments (
    comment_id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL,
    monitor_id INTEGER,
    reply_message TEXT,
    replied_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_replied_post ON replied_comments(post_id);
CREATE INDEX IF NOT EXISTS idx_replied_monitor ON replied_comments(monitor_id);

CREATE TABLE IF NOT EXISTS account_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL DEFAULT '',
    page_access_token TEXT NOT NULL,
    verify_token TEXT NOT NULL,
    page_id TEXT NOT NULL UNIQUE,
    api_version TEXT NOT NULL DEFAULT 'v25.0',
    is_active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_account_active ON account_configs(is_active);

CREATE TABLE IF NOT EXISTS model_configs (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    reply_api_base_url TEXT NOT NULL DEFAULT '',
    reply_api_key TEXT NOT NULL DEFAULT '',
    reply_model TEXT NOT NULL DEFAULT '',
    video_api_base_url TEXT NOT NULL DEFAULT '',
    video_api_key TEXT NOT NULL DEFAULT '',
    video_model TEXT NOT NULL DEFAULT '',
    prompt_template TEXT NOT NULL DEFAULT 'reply_prompt.j2',
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS admin_auth (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    password_iterations INTEGER NOT NULL DEFAULT 390000,
    force_password_change INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS admin_sessions (
    session_id TEXT PRIMARY KEY,
    expires_at TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT DEFAULT CURRENT_TIMESTAMP,
    ip TEXT,
    user_agent TEXT
);

CREATE INDEX IF NOT EXISTS idx_admin_sessions_expires_at ON admin_sessions(expires_at);

CREATE TABLE IF NOT EXISTS admin_login_attempts (
    ip TEXT PRIMARY KEY,
    failed_count INTEGER NOT NULL DEFAULT 0,
    first_failed_at TEXT,
    lock_until TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_admin_login_lock_until ON admin_login_attempts(lock_until);

CREATE TABLE IF NOT EXISTS auto_monitor_configs (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    enabled INTEGER NOT NULL DEFAULT 0,
    max_posts INTEGER NOT NULL DEFAULT 10,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS auto_monitor_schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger_time TEXT NOT NULL UNIQUE, -- HH:MM format
    enabled INTEGER NOT NULL DEFAULT 1,
    last_triggered_at TEXT -- YYYY-MM-DD HH:MM
);

CREATE TABLE IF NOT EXISTS page_conversations (
    id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL,
    updated_time TEXT,
    unread_count INTEGER DEFAULT 0,
    participants_json TEXT,
    synced_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (page_id) REFERENCES page_profiles(page_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    message_text TEXT,
    sender_id TEXT,
    sender_name TEXT,
    created_time TEXT,
    synced_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES page_conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_conv_page ON page_conversations(page_id);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON conversation_messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_msg_time ON conversation_messages(created_time);

CREATE TABLE IF NOT EXISTS video_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    post_time INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    pushed_at TEXT DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_video_analyses_post_id ON video_analyses(post_id);

CREATE TABLE IF NOT EXISTS comment_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    comment_id TEXT NOT NULL,
    media_type TEXT NOT NULL DEFAULT '',
    media_url TEXT NOT NULL,
    data BLOB,
    synced_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (comment_id) REFERENCES comments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_attachments_comment ON comment_attachments(comment_id);

CREATE TABLE IF NOT EXISTS schema_versions (
    version INTEGER PRIMARY KEY,
    applied_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    progress INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '{}',
    started_at TEXT,
    ended_at TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


from contextlib import contextmanager

@contextmanager
def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, autocommit=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode=WAL")
    try:
        yield connection
    finally:
        connection.close()


def init_db() -> None:
    with get_connection() as connection:
        connection.executescript(SCHEMA_SQL)

    with get_connection() as connection:
        _migrate_schema(connection)

    _seed_auto_monitor_config_if_needed()
    _seed_settings_from_legacy_json_if_needed()
    _seed_admin_auth_if_needed()


# ---------------------------------------------------------------------------
# Schema migration helpers
# ---------------------------------------------------------------------------

def _column_exists(connection, table: str, column: str) -> bool:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _get_schema_version(connection) -> int:
    try:
        row = connection.execute("SELECT MAX(version) FROM schema_versions").fetchone()
        return row[0] if row and row[0] is not None else 0
    except Exception:
        return 0


def _set_schema_version(connection, version: int) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO schema_versions (version) VALUES (?)", (version,)
    )


def _migrate_schema(connection) -> None:
    current = _get_schema_version(connection)

    # v2: add type, is_hidden to posts
    if current < 2:
        if not _column_exists(connection, "posts", "type"):
            connection.execute("ALTER TABLE posts ADD COLUMN type TEXT NOT NULL DEFAULT ''")
        if not _column_exists(connection, "posts", "is_hidden"):
            connection.execute("ALTER TABLE posts ADD COLUMN is_hidden INTEGER NOT NULL DEFAULT 0")
        _set_schema_version(connection, 2)

    # v3: add prompt_template to model_configs
    if current < 3:
        if not _column_exists(connection, "model_configs", "prompt_template"):
            connection.execute("ALTER TABLE model_configs ADD COLUMN prompt_template TEXT NOT NULL DEFAULT 'reply_prompt.j2'")
        _set_schema_version(connection, 3)

    # v4: add enabled to auto_monitor_schedules
    if current < 4:
        if not _column_exists(connection, "auto_monitor_schedules", "enabled"):
            connection.execute("ALTER TABLE auto_monitor_schedules ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
        _set_schema_version(connection, 4)

    # v5: add video_ai_model (legacy column, may have existed pre-reply/video split)
    if current < 5:
        if not _column_exists(connection, "model_configs", "video_ai_model"):
            connection.execute("ALTER TABLE model_configs ADD COLUMN video_ai_model TEXT NOT NULL DEFAULT ''")
        _set_schema_version(connection, 5)

    # v6: add pushed_at to video_analyses
    if current < 6:
        if not _column_exists(connection, "video_analyses", "pushed_at"):
            connection.execute("ALTER TABLE video_analyses ADD COLUMN pushed_at TEXT DEFAULT NULL")
        _set_schema_version(connection, 6)

    # v7: split model_configs into reply + video (add new columns)
    if current < 7:
        for col, col_type in [
            ("reply_api_base_url", "TEXT NOT NULL DEFAULT ''"),
            ("reply_api_key", "TEXT NOT NULL DEFAULT ''"),
            ("reply_model", "TEXT NOT NULL DEFAULT ''"),
            ("video_api_base_url", "TEXT NOT NULL DEFAULT ''"),
            ("video_api_key", "TEXT NOT NULL DEFAULT ''"),
            ("video_model", "TEXT NOT NULL DEFAULT ''"),
        ]:
            if not _column_exists(connection, "model_configs", col):
                connection.execute(f"ALTER TABLE model_configs ADD COLUMN {col} {col_type}")
        _set_schema_version(connection, 7)

    # v8: one-time data migration from old ai_* / video_ai_model columns to reply_*/video_*
    if current < 8:
        if _column_exists(connection, "model_configs", "ai_api_base_url"):
            connection.execute(
                "UPDATE model_configs SET "
                "reply_api_base_url = COALESCE(NULLIF(ai_api_base_url, ''), reply_api_base_url), "
                "reply_api_key = COALESCE(NULLIF(ai_api_key, ''), reply_api_key), "
                "reply_model = COALESCE(NULLIF(ai_model, ''), reply_model), "
                "video_model = COALESCE(NULLIF(video_ai_model, ''), video_model), "
                "video_api_base_url = COALESCE(NULLIF(ai_api_base_url, ''), video_api_base_url), "
                "video_api_key = COALESCE(NULLIF(ai_api_key, ''), video_api_key) "
                "WHERE reply_api_base_url = '' OR reply_api_key = '' OR reply_model = ''"
            )
        _set_schema_version(connection, 8)

    # v9: add screened to comments
    if current < 9:
        if not _column_exists(connection, "comments", "screened"):
            connection.execute("ALTER TABLE comments ADD COLUMN screened INTEGER NOT NULL DEFAULT 0")
        _set_schema_version(connection, 9)

    # v10: add data BLOB to comment_attachments
    if current < 10:
        if not _column_exists(connection, "comment_attachments", "data"):
            connection.execute("ALTER TABLE comment_attachments ADD COLUMN data BLOB")
        _set_schema_version(connection, 10)

    # v11: create tasks table for unified task management
    if current < 11:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                progress INTEGER NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                result TEXT NOT NULL DEFAULT '{}',
                started_at TEXT,
                ended_at TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        _set_schema_version(connection, 11)

    # v12: add app_secret to model_configs for webhook signature verification
    if current < 12:
        if not _column_exists(connection, "model_configs", "app_secret"):
            connection.execute("ALTER TABLE model_configs ADD COLUMN app_secret TEXT NOT NULL DEFAULT ''")
        _set_schema_version(connection, 12)

    # v13: move app_secret from model_configs to account_configs
    if current < 13:
        if not _column_exists(connection, "account_configs", "app_secret"):
            connection.execute("ALTER TABLE account_configs ADD COLUMN app_secret TEXT NOT NULL DEFAULT ''")
            # Migrate existing data: copy app_secret from model_configs to active account
            row = connection.execute("SELECT app_secret FROM model_configs LIMIT 1").fetchone()
            if row and row["app_secret"]:
                active = connection.execute("SELECT id FROM account_configs WHERE is_active = 1 LIMIT 1").fetchone()
                if active:
                    connection.execute("UPDATE account_configs SET app_secret = ? WHERE id = ?", (row["app_secret"], active["id"]))
                else:
                    connection.execute("UPDATE account_configs SET app_secret = ? WHERE id = (SELECT MIN(id) FROM account_configs)", (row["app_secret"],))
        _set_schema_version(connection, 13)

    # v14: add task_type to tasks for Task Center categorization
    if current < 14:
        if not _column_exists(connection, "tasks", "task_type"):
            connection.execute("ALTER TABLE tasks ADD COLUMN task_type TEXT NOT NULL DEFAULT ''")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_tasks_type_status ON tasks(task_type, status)")
        _set_schema_version(connection, 14)


def _seed_auto_monitor_config_if_needed() -> None:
    with get_connection() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO auto_monitor_configs (id, enabled, max_posts) VALUES (1, 0, 10)"
        )


def _seed_settings_from_legacy_json_if_needed() -> None:
    """Seed settings tables from legacy config.json on first startup.

    Runtime config is now DB-driven; legacy JSON is used only as bootstrap data.
    """
    with get_connection() as connection:
        count = connection.execute("SELECT COUNT(*) FROM account_configs").fetchone()[0]
        if count:
            return

    raw = _load_json(PROJECT_ROOT / "config.json", {})

    page_id = str(raw.get("PAGE_ID", "")).strip()
    if not page_id:
        page_id = "default-page"

    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO account_configs (
                name, page_access_token, verify_token, page_id, api_version, is_active, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
            """,
            (
                "默认账号",
                str(raw.get("PAGE_ACCESS_TOKEN", "")),
                str(raw.get("VERIFY_TOKEN", "")),
                page_id,
                str(raw.get("API_VERSION", "v25.0")) or "v25.0",
            ),
        )

        connection.execute(
            """
            INSERT INTO model_configs (
                id, reply_api_base_url, reply_api_key, reply_model, updated_at
            )
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                reply_api_base_url = excluded.reply_api_base_url,
                reply_api_key = excluded.reply_api_key,
                reply_model = excluded.reply_model,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                1,
                str(raw.get("AI_API_BASE_URL", "")),
                str(raw.get("AI_API_KEY", "")),
                str(raw.get("AI_MODEL", "")),
            ),
        )


def _seed_admin_auth_if_needed() -> None:
    with get_connection() as connection:
        row = connection.execute("SELECT id FROM admin_auth WHERE id = 1").fetchone()
        if row:
            return

        username = "admin"
        env_password = str(os.getenv("ADMIN_PASSWORD", "")).strip()

        if not env_password or not is_strong_password(env_password):
            raise RuntimeError("首次启动请设置强密码环境变量 ADMIN_PASSWORD（至少16位，包含大小写字母、数字和符号）")

        password = env_password
        force_change = 0
        logger.info("[auth] admin account initialized from ADMIN_PASSWORD")

        salt = generate_salt()
        password_hash = hash_password(password, salt, PBKDF2_ITERATIONS)

        connection.execute(
            """
            INSERT INTO admin_auth (
                id, username, password_hash, password_salt, password_iterations, force_password_change, updated_at
            )
            VALUES (1, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (username, password_hash, salt, PBKDF2_ITERATIONS, force_change),
        )

def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default

    with path.open("r", encoding="utf-8") as file:
        try:
            return json.load(file)
        except json.JSONDecodeError:
            return default


def migrate_legacy_json_if_needed() -> bool:
    from app.repositories import upsert_post, upsert_page_profile, replace_comments_for_post

    with get_connection() as connection:
        post_count = connection.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        comment_count = connection.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
        profile_count = connection.execute("SELECT COUNT(*) FROM page_profiles").fetchone()[0]

    if post_count or comment_count or profile_count:
        return False

    posts = _load_json(POSTS_JSON, [])
    comments_by_post = _load_json(COMMENTS_JSON, {})
    if not posts and not comments_by_post:
        return False

    page_id = posts[0].get("id", "").split("_")[0] if posts else "legacy-page"
    upsert_page_profile(
        {
            "id": page_id,
            "name": "Legacy Imported Page",
            "username": "",
            "link": "",
            "category": "",
            "fan_count": 0,
            "picture": {"data": {"url": ""}},
        }
    )

    for post in posts:
        upsert_post(page_id, post)
        replace_comments_for_post(post["id"], comments_by_post.get(post["id"], []))

    return True
