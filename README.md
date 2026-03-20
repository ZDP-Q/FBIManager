# FBIManager (Facebook Interaction Manager)

[中文版本 (Chinese Version)](./README.zh-CN.md)

A FastAPI-based Facebook interaction management system integrated with AI automated replies and multi-account monitoring.

### Core Features

- **Security First**:
  - Administrator-based authentication using PBKDF2-SHA256 with 390,000 iterations.
  - Built-in IP-based login locking to prevent brute-force attacks (locks after multiple failed attempts).
  - Secure session management with HTTP-only and SameSite cookies.
  - CSRF protection for core write operations via mandatory origin/referer checks.
  - Strict security response headers (CSP, X-Frame-Options, etc.).
- **Intelligent Monitoring & Reply**:
  - **Exclusive AI Persona (Elio)**: The AI portrays "Elio," a 35-year-old, confident, and charismatic investor, providing natural and engaging social interactions.
  - **Enhanced Synchronization**: Deeply adapted to Facebook Graph API v25.0, supporting a multi-level edge fallback strategy (`published_posts` -> `posts` -> `feed`) for high reliability.
  - **Smart Re-generation**: Detects if an AI reply was manually deleted on the Facebook web interface and allows the system to re-generate or re-send the reply, ensuring interaction integrity.
- **Management Capabilities**:
  - **Multi-Account Support**: Flexible switching between monitoring and reply tasks for different Page IDs.
  - **Configuration Decoupling**: Account settings (Access Tokens, etc.) and model configurations are managed independently and take effect immediately.
  - **Visual Dashboard**: Provides intuitive views for comment management, post monitoring, and synchronization status.
  - **Connectivity Test**: Built-in "Test Configuration" button to verify LLM API connectivity and model settings before saving.

### Project Architecture

- `app/auth.py` & `app/security.py`: Core authentication and encryption libraries.
- `app/services/facebook.py`: Highly encapsulated Graph API v25.0 client.
- `app/services/monitor.py`: Intelligent monitoring engine based on task scheduling.
- `app/services/ai_reply.py`: Automated reply logic interfacing with Large Language Models (LLMs).
- `data/facebookmsg.sqlite3`: SQLite database storing posts, comments, replies, and system configurations.
- `reset_pwd.py`: CLI tool for initializing or resetting the administrator password.

### Quick Start

1. **Install Dependencies**:
   ```bash
   uv sync
   ```

2. **Initialize/Reset Password**:
   ```bash
   uv run python reset_pwd.py
   ```
   *The default username is `admin`. A strong password (at least 16 characters) is required during the initial setup or environment change.*

3. **Run the Application**:
   ```bash
   uv run python main.py
   ```
   Access `http://127.0.0.1:8000` and log in with your administrator account.

### Docker Deployment

A pre-configured Docker setup is available in the `docker/` directory:
- The `ADMIN_PASSWORD` is centrally managed in `docker/start.sh` (must be a strong password of 16+ characters).
- Persistent data and logs are stored in host directories via volumes.

### Configuration Guide

Manage settings via the **Configuration Panel** or **Security Settings** on the home page:
- **Security Settings**: Change the administrator's current password.
- **Account Configuration**: Set `PAGE_ACCESS_TOKEN`, `PAGE_ID`, and `API_VERSION` (v25.0 recommended).
- **Model Configuration**: Configure the API endpoint, API key, model name, and the AI System Prompt. Use the **Test Configuration** button to verify connectivity.

### Important Notes

- **Database Backup**: Regularly back up `data/facebookmsg.sqlite3` in production environments.
- **API Permissions**: Ensure your Page Access Token has the necessary permissions, such as `pages_manage_metadata`, `pages_read_engagement`, and `pages_messaging`.
