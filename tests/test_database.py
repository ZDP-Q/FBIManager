"""Tests for database initialization, schema, and connection management."""
import os
import sqlite3

import pytest


def _setup_schema():
    """Create schema and run migrations. Call at start of tests that need tables."""
    from app.database import SCHEMA_SQL, get_connection

    with get_connection() as conn:
        conn.executescript(SCHEMA_SQL)
    _run_migrations()


def _run_migrations():
    from app.database import get_connection

    migrations = [
        "ALTER TABLE posts ADD COLUMN type TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE posts ADD COLUMN is_hidden INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE model_configs ADD COLUMN prompt_template TEXT NOT NULL DEFAULT 'reply_prompt.j2'",
        "ALTER TABLE auto_monitor_schedules ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE model_configs ADD COLUMN video_ai_model TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE video_analyses ADD COLUMN pushed_at TEXT DEFAULT NULL",
        "ALTER TABLE model_configs ADD COLUMN reply_api_base_url TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE model_configs ADD COLUMN reply_api_key TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE model_configs ADD COLUMN reply_model TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE model_configs ADD COLUMN video_api_base_url TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE model_configs ADD COLUMN video_api_key TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE model_configs ADD COLUMN video_model TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE comments ADD COLUMN screened INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE comment_attachments ADD COLUMN data BLOB",
    ]
    with get_connection() as conn:
        for sql in migrations:
            try:
                conn.execute(sql)
            except Exception:
                pass
        try:
            conn.execute(
                "UPDATE model_configs SET "
                "reply_api_base_url = COALESCE(NULLIF(ai_api_base_url, ''), reply_api_base_url), "
                "reply_api_key = COALESCE(NULLIF(ai_api_key, ''), reply_api_key), "
                "reply_model = COALESCE(NULLIF(ai_model, ''), reply_model), "
                "video_model = COALESCE(NULLIF(video_ai_model, ''), video_model), "
                "video_api_base_url = COALESCE(NULLIF(ai_api_base_url, ''), video_api_base_url), "
                "video_api_key = COALESCE(NULLIF(ai_api_key, ''), video_api_key) "
                "WHERE reply_api_base_url = '' OR reply_api_key = '' OR reply_model = ''"
            )
        except Exception:
            pass


class TestInitDb:
    def test_init_db_creates_all_tables(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "TestAdminPassword123!@#$")
        from app.database import get_connection, init_db

        init_db()

        with get_connection() as conn:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }

        expected = {
            "page_profiles", "posts", "comments", "post_monitors",
            "replied_comments", "account_configs", "model_configs",
            "admin_auth", "admin_sessions", "admin_login_attempts",
            "auto_monitor_configs", "auto_monitor_schedules",
            "page_conversations", "conversation_messages",
            "video_analyses", "comment_attachments",
        }
        missing = expected - tables
        assert not missing, f"Missing tables: {missing}"

    def test_init_db_idempotent(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "TestAdminPassword123!@#$")
        from app.database import init_db

        init_db()
        init_db()  # Second call should not raise


class TestSeedFunctions:
    def test_seed_auto_monitor_config(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "TestAdminPassword123!@#$")
        _setup_schema()
        from app.database import _seed_auto_monitor_config_if_needed, get_connection

        _seed_auto_monitor_config_if_needed()

        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM auto_monitor_configs WHERE id = 1"
            ).fetchone()
            assert row is not None
            assert row["enabled"] == 0
            assert row["max_posts"] == 10

    def test_seed_admin_auth_requires_env_password(self, monkeypatch):
        monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
        _setup_schema()
        from app.database import _seed_admin_auth_if_needed

        with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
            _seed_admin_auth_if_needed()

    def test_seed_admin_auth_succeeds_with_valid_password(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "StrongP@ssw0rd12345")
        _setup_schema()
        from app.database import _seed_admin_auth_if_needed, get_connection

        _seed_admin_auth_if_needed()

        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM admin_auth WHERE id = 1"
            ).fetchone()
            assert row is not None
            assert row["username"] == "admin"
            assert row["password_hash"]
            assert row["password_salt"]

    def test_seed_admin_auth_rejects_weak_password(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "short")
        _setup_schema()
        from app.database import _seed_admin_auth_if_needed

        with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
            _seed_admin_auth_if_needed()

    def test_seed_admin_auth_skips_when_exists(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "StrongP@ssw0rd12345")
        _setup_schema()
        from app.database import _seed_admin_auth_if_needed, get_connection

        _seed_admin_auth_if_needed()

        # Change password — shouldn't matter since admin already exists
        monkeypatch.setenv("ADMIN_PASSWORD", "AnotherStrongPass123!")
        _seed_admin_auth_if_needed()  # Should not raise, is a no-op

        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM admin_auth WHERE id = 1"
            ).fetchone()
            assert row is not None
            assert row["password_salt"]


class TestGetConnection:
    def test_opens_and_closes(self):
        from app.database import get_connection

        with get_connection() as conn:
            assert isinstance(conn, sqlite3.Connection)
            conn.execute("SELECT 1")

    def test_row_factory_is_set(self):
        from app.database import get_connection

        with get_connection() as conn:
            row = conn.execute("SELECT 1 AS value").fetchone()
            assert row["value"] == 1
            assert row[0] == 1

    def test_foreign_keys_enabled(self):
        from app.database import get_connection

        with get_connection() as conn:
            fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
            assert fk == 1


class TestMigrations:
    def test_migration_add_screened_column(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "TestAdminPassword123!@#$")
        from app.database import SCHEMA_SQL, get_connection

        with get_connection() as conn:
            conn.executescript(SCHEMA_SQL)
        try:
            with get_connection() as conn:
                conn.execute("ALTER TABLE comments DROP COLUMN screened")
        except Exception:
            pass

        from app.database import init_db

        init_db()

        with get_connection() as conn:
            cols = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(comments)").fetchall()
            }
            assert "screened" in cols

    def test_migration_add_data_blob(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "TestAdminPassword123!@#$")
        from app.database import SCHEMA_SQL, get_connection

        with get_connection() as conn:
            conn.executescript(SCHEMA_SQL)
        try:
            with get_connection() as conn:
                conn.execute("ALTER TABLE comment_attachments DROP COLUMN data")
        except Exception:
            pass

        from app.database import init_db

        init_db()

        with get_connection() as conn:
            cols = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(comment_attachments)"
                ).fetchall()
            }
            assert "data" in cols

    def test_migration_columns_exist_after_init(self, monkeypatch):
        monkeypatch.setenv("ADMIN_PASSWORD", "TestAdminPassword123!@#$")
        from app.database import get_connection, init_db

        init_db()

        with get_connection() as conn:
            post_cols = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(posts)").fetchall()
            }
            assert "type" in post_cols
            assert "is_hidden" in post_cols

            mc_cols = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(model_configs)"
                ).fetchall()
            }
            for col in ("reply_api_base_url", "reply_api_key", "reply_model",
                        "video_api_base_url", "video_api_key", "video_model",
                        "prompt_template"):
                assert col in mc_cols, f"Missing column: {col}"