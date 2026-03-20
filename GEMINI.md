# FBIManager Project Context

## Project Overview
FBIManager (Facebook Interaction Manager) is a FastAPI-based application designed to manage Facebook Page interactions. It automates the synchronization of posts and comments, provides real-time monitoring of specific posts, and employs Large Language Models (LLMs) to generate intelligent automated replies.

### Core Technologies
- **Backend:** Python 3.12+, FastAPI, Uvicorn, HTTPX (for Graph API and LLM requests).
- **Database:** SQLite (local file-based storage for posts, comments, and system configs).
- **Frontend:** Jinja2 templates, Vanilla CSS, and Vanilla JavaScript (no heavy frontend frameworks).
- **Environment Management:** [uv](https://github.com/astral-sh/uv) is used for dependency management and script execution.
- **AI Integration:** Compatible with OpenAI-style Chat Completion APIs (e.g., GPT, Qwen, DeepSeek).

### Key Architecture Components
- `app/application.py`: App factory, lifespan management (DB init, monitor start/stop), and security middleware (Auth, CSRF protection, CSP).
- `app/services/facebook.py`: Encapsulates Facebook Graph API v25.0 interactions.
- `app/services/monitor.py`: Background task scheduler that periodically scans monitored posts for new comments.
- `app/services/ai_reply.py`: Logic for generating AI responses based on post and comment context.
- `app/repositories.py`: Data Access Object (DAO) layer for SQLite interactions.

## Building and Running

### Prerequisites
- Python 3.12 or higher.
- `uv` installed (`pip install uv` or via standalone installer).

### Key Commands
- **Install Dependencies:**
  ```bash
  uv sync
  ```
- **Initialize/Reset Admin Password:**
  ```bash
  # Must be done before the first run. Required 16+ characters.
  uv run python reset_pwd.py
  ```
- **Run the Application (Development):**
  ```bash
  uv run python main.py
  ```
- **Deployment (Docker):**
  ```bash
  # Host-side deployment script using docker compose
  bash scripts/deploy.sh
  ```

### Startup Script
- `scripts/start.sh`: Unified entry point for both Docker containers and host-side execution. It automatically detects the environment and handles working directory switching.

## Development Conventions

### Security Standards
- **Authentication:** Administrator access is protected by PBKDF2-SHA256 hashing. Login attempts are throttled/locked by IP.
- **CSRF Protection:** Middleware strictly validates `Origin` and `Referer` headers for all mutation requests (`POST`, `PUT`, `PATCH`, `DELETE`).
- **Data Privacy:** Never hardcode Access Tokens or API Keys. Use the web interface to configure them, where they are stored in the local SQLite database.

### Implementation Guidelines
- **Python Style:** Use Python 3.12 features (e.g., `dataclass(slots=True)`, type hints). Adhere to `ruff` for linting and formatting.
- **Facebook API:** Prefer Graph API v25.0. Use the fallback strategy in `FacebookService` to ensure high fetch success rates across different page types.
- **Async First:** All network I/O (Facebook API, LLM calls) and database operations must be asynchronous where possible to avoid blocking the event loop.
- **UI Logic:** Keep the frontend simple. Use Vanilla JS and interactive feedback (alerts, loading states) for all asynchronous actions.

### Testing and Validation
- **Connectivity Testing:** Always use the "Test Configuration" feature in the UI when updating LLM settings.
- **Manual Verification:** After significant changes to sync or monitor logic, manually trigger a sync/run in the UI to verify behavioral correctness.
