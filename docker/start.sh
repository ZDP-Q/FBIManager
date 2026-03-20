#!/bin/sh
# 容器内部的启动脚本
# 确保在出错时立即退出
set -e

# 在 Dockerfile 中，WORKDIR 已经设置为 /app
# 如果是从容器内部运行，我们确保切换到 /app 根目录
cd /app

# --- 设置管理员密码 ---
# 您可以直接在此处设置管理员强密码（必须 16 位以上，包含大小写字母、数字和特殊字符）
export ADMIN_PASSWORD="FbManager@StrongPass2026"

# 执行初始化/重置密码逻辑
echo "Running reset_pwd.py with ADMIN_PASSWORD set in container using uv..."
uv run python reset_pwd.py

# 使用 exec 启动主程序，使 Python 成为 PID 1 并能接收 SIGTERM 信号
echo "Starting application with uv run..."
exec uv run python main.py
