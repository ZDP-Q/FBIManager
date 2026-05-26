"""Shared test fixtures for FBIManager."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Registry reset (autouse)
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def reset_registry():
    """Reset module-level singletons between tests."""
    from app import registry

    registry._monitor_service = None
    registry._task_statuses.clear()


# ---------------------------------------------------------------------------
# Database isolation
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    """Temporary SQLite database path."""
    db_dir = tmp_path / "data"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "facebookmsg.sqlite3"


@pytest.fixture(autouse=True)
def patch_db_path(tmp_db_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect DB_PATH to temp file for every test."""
    import app.database

    monkeypatch.setattr(app.database, "DB_PATH", tmp_db_path)


# ---------------------------------------------------------------------------
# Schema initialization helper (reusable across test modules)
# ---------------------------------------------------------------------------
@pytest.fixture
def setup_db():
    """Create schema, run migrations, seed admin. For tests that need a full DB."""
    import app.database

    os.environ["ADMIN_PASSWORD"] = "TestAdminPassword123!@#$"

    with app.database.get_connection() as conn:
        conn.executescript(app.database.SCHEMA_SQL)

    # Run migrations
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
    with app.database.get_connection() as conn:
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

    app.database._seed_auto_monitor_config_if_needed()
    app.database._seed_admin_auth_if_needed()


# ---------------------------------------------------------------------------
# FastAPI app fixture (no lifespan — no MonitorService background loop)
# ---------------------------------------------------------------------------
@pytest.fixture
def test_app(setup_db):
    """Create FastAPI app with DB initialized but no lifespan (no background tasks)."""
    from app.application import create_app

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _noop_lifespan(app):
        yield

    app = create_app()
    app.router.lifespan_context = _noop_lifespan
    return app


# ---------------------------------------------------------------------------
# TestClient fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def client(test_app):
    """Unauthenticated TestClient."""
    with TestClient(test_app) as c:
        yield c


@pytest.fixture
def auth_client(test_app):
    """Authenticated TestClient with valid admin session."""
    with TestClient(test_app) as c:
        resp = c.post("/login", data={"password": "TestAdminPassword123!@#$", "next": "/"}, follow_redirects=False)
        assert resp.status_code == 303
        yield c