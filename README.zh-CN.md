# FBIManager (Facebook 互动管理系统)

[English Version](./README.md)

一个基于 FastAPI 的 Facebook 互动管理系统，集成 AI 自动回复与多账号监控功能。

### 核心特性

- **安全性优先**：
  - **高级身份验证**：基于管理员账号的身份验证，使用 PBKDF2-SHA256 算法及 39 万次迭代。
  - **防暴力破解**：内置基于 IP 的登录锁定机制，多次错误尝试后将自动锁定。
  - **安全会话**：采用 HTTP-only 和 SameSite 属性的安全 Cookie 会话管理。
  - **CSRF 防护**：对所有核心写入操作实施强制同源 (Origin/Referer) 校验，有效防御跨站请求伪造。
  - **安全响应头**：配置严格的 CSP、X-Frame-Options 等安全响应头。
- **智能监控与回复**：
  - **专属 AI 人设 (Elio)**：AI 扮演一名 35 岁、自信且有魅力的成熟投资人 "Elio"，提供自然且极具社交吸引力的回复。
  - **同步增强**：深度适配 Facebook Graph API v25.0，支持多层级 Edge 回退策略（published_posts -> posts -> feed），确保数据同步的高可靠性。
  - **智能补发机制**：能够检测在 Facebook 网页端被手动删除的 AI 回复，并支持系统重新生成或补发，确保互动的完整性。
- **管理能力**：
  - **多账号支持**：可灵活切换不同 Page ID 的监控与回复任务。
  - **配置分离**：账号配置（Access Token 等）与模型配置（OpenAI 兼容 API、提示词等）独立管理，修改即时生效。
  - **可视化面板**：提供直观的评论中心、帖子监控和同步状态展示。
  - **连通性测试**：内置“测试配置”按钮，在保存前可实时验证 LLM API 的连通性和模型设置。

### 项目架构

- `app/auth.py` & `app/security.py`：核心身份验证与加密库。
- `app/services/facebook.py`：高度封装的 Graph API v25.0 客户端。
- `app/services/monitor.py`：基于任务调度的智能监控引擎。
- `app/services/ai_reply.py`：对接大语言模型 (LLM) 的自动回复逻辑。
- `data/facebookmsg.sqlite3`：SQLite 数据库，存储帖子、评论、回复及系统配置。
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
   *默认用户名为 `admin`。首次运行或环境变更时，必须设置 16 位及以上的强密码。*

3. **运行应用**：
   ```bash
   uv run python main.py
   ```
   访问 `http://127.0.0.1:8000` 并使用管理员账号登录。

### Docker 部署

项目在 `docker/` 目录下提供了预配置的 Docker 部署方案：
- 管理员密码 `ADMIN_PASSWORD` 在 `docker/start.sh` 脚本中统一管理（需设置为 16 位以上强密码）。
- 持久化数据和日志通过 Volume 映射到宿主机目录。

### 配置说明

在首页的**配置面板**或**安全设置**中进行维护：
- **安全设置**：修改管理员当前密码。
- **账号配置**：配置 `PAGE_ACCESS_TOKEN`、`PAGE_ID` 和 `API_VERSION` (建议使用 v25.0)。
- **模型配置**：配置 API 地址、API 密钥、模型名称以及 AI 系统提示词 (Prompt)。使用**测试配置**按钮验证连通性。

### 注意事项

- **数据库备份**：生产环境下请定期备份 `data/facebookmsg.sqlite3` 数据库文件。
- **API 权限**：确保使用的 Page Access Token 拥有 `pages_manage_metadata`、`pages_read_engagement` 和 `pages_messaging` 等必要权限。
