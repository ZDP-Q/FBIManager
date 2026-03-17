## FacebookMsg

一个基于 FastAPI 的 Facebook 互动管理后台。

### 当前架构

- `data/facebookmsg.sqlite3`：帖子、评论、回复、主页信息，以及账号/模型配置的 SQLite 数据库
- `app/services`：Facebook Graph API、AI 回复、同步编排
- `app/routes`：页面路由与 API 路由
- `templates` + `static`：前端模板、样式和脚本

说明：`config.json` 已弃用为运行时配置来源，仅在数据库首次初始化时用于导入默认配置。

### 功能

- 首页概览：通过 API 获取并展示当前主页资料
- 评论中心：查看帖子、评论、回复
- 手动回复评论
- AI 生成回复文案
- 删除评论或回复
- 一键同步 Facebook 数据到 SQLite
- 多账号配置（通过 `PAGE_ID` + `PAGE_ACCESS_TOKEN` 区分）
- 账号配置与模型配置分离，并可在 Web 页面直接保存

### 启动

```bash
uv sync
uv run python main.py
```

打开浏览器访问：`http://127.0.0.1:8000`

### 配置方式

在首页的配置面板中维护：

- 账号配置：`PAGE_ACCESS_TOKEN`、`VERIFY_TOKEN`、`PAGE_ID`、`API_VERSION`
- 模型配置：`AI_API_BASE_URL`、`AI_API_KEY`、`AI_MODEL`、`AI_SYSTEM_PROMPT`
