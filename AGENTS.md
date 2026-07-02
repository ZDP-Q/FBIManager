# Repository Guidelines

## Project Structure & Module Organization

FBIManager is a FastAPI application. `main.py` creates the server from `app/application.py` and serves on port `38000`. Core backend code lives in `app/`: routes in `app/routes/`, service logic in `app/services/`, repositories in `app/repos/` and `app/repositories.py`, and configuration/database helpers in `app/config.py` and `app/database.py`. Jinja2 templates are in `templates/`, browser assets in `static/css/` and `static/js/`, AI persona prompts in `prompts/`, and deployment files in `docker/` and `scripts/`. Backend tests live in `tests/`; frontend tests use `*.test.js` under `static/js/`.

## Build, Test, and Development Commands

- `uv sync --extra test`: install Python dependencies and pytest tooling.
- `uv run python reset_pwd.py`: initialize or reset the admin password; set `ADMIN_PASSWORD` first and keep it at least 16 characters.
- `uv run python main.py`: run the app locally at `http://127.0.0.1:38000`.
- `uv run pytest`: run the Python test suite configured in `pyproject.toml`.
- `npm install`: install JavaScript dev dependencies from `package-lock.json`.
- `npm run test:js`: run Vitest/jsdom tests.
- `docker compose -f docker/docker-compose.yml up --build`: build and run the containerized app.

## Coding Style & Naming Conventions

Use Python 3.12 syntax and 4-space indentation. Follow existing naming: modules and functions use `snake_case`, classes use `PascalCase`, and constants use `UPPER_SNAKE_CASE`. Keep route handlers thin; put Graph API, AI reply, synchronization, and monitor behavior in services. Prefer standard-library solutions unless the project already has a relevant dependency. JavaScript files in `static/js/` use CommonJS-compatible tooling.

## Testing Guidelines

Add or update pytest coverage in `tests/test_*.py`; shared fixtures belong in `tests/conftest.py` and factories in `tests/factories.py`. Use `pytest-asyncio` for async FastAPI/service tests and `pytest-httpx` for outbound HTTP mocks. For browser scripts, place Vitest tests next to related files as `static/js/name.test.js`. Run both `uv run pytest` and `npm run test:js` when changes touch Python and JavaScript.

## Commit & Pull Request Guidelines

Recent history uses short Conventional Commit-style prefixes such as `fix:`, `feat:`, `refactor:`, and `ui:`. Keep subjects imperative and specific, for example `fix: handle expired webhook sessions`. Pull requests should describe the change, list tests run, link issues, and include screenshots for template or static UI changes.

## Security & Configuration Tips

Do not commit production databases, logs, page tokens, webhook secrets, or admin passwords. Runtime data is expected under `data/` and `logs/`; back up `data/facebookmsg.sqlite3` before migrations or production deploys. Preserve the existing authentication, CSRF, and security-header behavior when editing middleware or API write routes.
