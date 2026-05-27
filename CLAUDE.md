# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

FBIManager (Facebook Interaction Manager) — a FastAPI app for managing Facebook Page interactions with AI-powered automated replies. Uses Facebook Graph API v25.0, OpenAI-compatible LLMs, and SQLite. UI and comments are in Simplified Chinese.

## Commands

```bash
# Install dependencies
uv sync

# Run in development (port 38000)
uv run python main.py

# Run tests
uv run pytest

# Run a single test file
uv run pytest tests/test_repositories.py

# Run a single test
uv run pytest tests/test_repositories.py::test_function_name

# Run tests with coverage
uv run pytest --cov=app --cov-report=term-missing

# Reset admin password (must be 16+ chars)
export ADMIN_PASSWORD="your-strong-password-here"
uv run python reset_pwd.py

# Deploy with Docker
bash scripts/deploy.sh

# View Docker logs
docker compose -f docker/docker-compose.yml logs -f
```

## Architecture

**App factory:** `app/application.py` — `create_app()` with lifespan that initializes DB, migrates legacy JSON config, starts MonitorService scheduler.

**Layered structure:**
- `app/routes/` — HTTP endpoints (`web.py` for pages, `api.py` for REST under `/api`, `webhook.py` for Facebook events)
- `app/services/` — Business logic (Facebook API, AI replies, sync, monitoring, chat sync)
- `app/repositories.py` — DAO layer, all SQLite CRUD with UPSERT for bulk imports
- `app/database.py` — Schema (16 tables), connection management, migrations
- `app/config.py` — AppConfig dataclass loaded from DB (legacy JSON auto-migrated)
- `app/registry.py` — Global singleton registry for MonitorService and task status tracking
- `app/security.py` — PBKDF2-SHA256 hashing, session ID generation

**Key patterns:**
- Long-running operations (post sync, chat sync) report progress via `registry.update_task_status()` and stream to frontend via SSE (`/api/sync/stream`, `/api/chats/sync`)
- AI personas are Jinja2 templates in `prompts/*.j2`, rendered with page/post/comment context, sent to OpenAI-compatible Chat Completion API
- Facebook posts use edge fallback: `published_posts` -> `posts` -> `feed`
- All network I/O is async (httpx); all DB operations use connection context manager from `database.py`

**Frontend:** Jinja2 HTML templates in `templates/`, vanilla JS in `static/js/`, single CSS file `static/css/style.css`. Each page has a matching JS file.

**Config storage:** SQLite tables (`account_configs`, `model_configs`, `admin_auth`), not env vars. `ADMIN_PASSWORD` env var is only for `reset_pwd.py`.

## Testing

Tests use **pytest** with `pytest-asyncio`, `pytest-httpx`, and `pytest-cov`. Key conventions:

- `asyncio_mode = "auto"` — all `async def test_*` functions run as async tests automatically
- Each test gets an isolated temporary SQLite database via the autouse `patch_db_path` fixture
- The `setup_db` fixture initializes schema + migrations + seeds admin auth
- `client` fixture = unauthenticated TestClient; `auth_client` fixture = logged-in admin session
- The app fixture disables the MonitorService background loop (no lifespan)

## Development Conventions

- Python 3.12+ features (e.g., `dataclass(slots=True)`)
- All services handle multiple Facebook Page configs dynamically via `repositories.py`
- Background tasks must update `registry.update_task_status()` for UI visibility
- Session cookies: HTTP-only, SameSite=strict, 8-hour TTL
- CSRF: Origin/Referer validation on API writes
- CSP headers defined in `application.py` — `unsafe-inline` required for JS/CSS
- Public paths (no auth): `/login`, `/favicon.ico`, `/static/*`, `/webhook*`
