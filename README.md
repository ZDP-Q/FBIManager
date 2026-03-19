# Facebook Interaction Manager (FBManager)

一个基于 FastAPI 的 Facebook 互动管理系统，集成 AI 自动回复与多账号监控。

### 核心特性

- **安全性优先**：
  - 基于管理员账号的身份验证（PBKDF2-SHA256，39万次迭代）。
  - 内置 IP 限制机制，防止暴力破解（错误尝试多次后锁定）。
  - HTTP-only/SameSite 安全 Cookie 会话管理。
  - 核心操作（API 写入）强制同源校验，防御 CSRF。
  - 安全响应头设置（CSP, X-Frame-Options 等）。
- **智能监控与回复**：
  - **Elio 专属人设**：AI 扮演一名 35 岁、自信且有魅力的成熟投资人，提供自然的社交回复。
  - **同步增强**：深度适配 Facebook Graph API v25.0，支持多层级 Edge 回退策略（published_posts -> posts -> feed）。
  - **智能重录机制**：检测在 Facebook 网页端被手动删除的 AI 回复，并允许系统重新生成/补发回复，确保互动完整。
- **管理能力**：
  - **多账号支持**：灵活切换不同 Page ID 的监控与回复任务。
  - **配置分离**：账号配置（Access Token 等）与模型配置（OpenAI-like API, Prompt）独立管理，即时生效。
  - **可视化面板**：提供直观的评论中心、帖子监控和同步状态展示。

### 项目架构

- `app/auth.py` & `app/security.py`：核心身份验证与加密库。
- `app/services/facebook.py`：高度封装的 Graph API v25.0 客户端。
- `app/services/monitor.py`：基于任务调度的智能监控引擎。
- `app/services/ai_reply.py`：对接大语言模型的自动回复逻辑。
- `data/facebookmsg.sqlite3`：存储帖子、评论、回复及系统配置。
- `reset_pwd.py`：用于初始化或重置管理员密码的 CLI 工具。

### 快速启动

1. **安装依赖**：
   ```bash
   uv sync
   ```

2. **初始化/重置密码**：
   ```bash
   uv run python reset_pwd.py
   ```
   *默认用户名为 `admin`。首次运行或环境变更时需手动设置强密码。*

3. **运行应用**：
   ```bash
   uv run python main.py
   ```
   访问 `http://127.0.0.1:8000` 并使用管理员账号登录。

### 配置说明

在首页的**配置面板**或**安全设置**中进行维护：
- **安全设置**：修改管理员当前密码（需符合 16 位及以上强密码要求）。
- **账号配置**：`PAGE_ACCESS_TOKEN`、`PAGE_ID`、`API_VERSION` (建议 v25.0)。
- **模型配置**：API 地址、密钥、模型名称以及 AI 系统提示词（Prompt）。

### 注意事项

- **数据库备份**：生产环境下请定期备份 `data/facebookmsg.sqlite3`。
- **API 权限**：确保 Page Access Token 拥有 `pages_manage_metadata`、`pages_read_engagement` 和 `pages_messaging` 等必要权限。
