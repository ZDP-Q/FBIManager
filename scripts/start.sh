#!/bin/bash
# 统一启动脚本 (支持容器内及宿主机直接运行)
set -e

# 获取脚本所在的绝对路径
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
# 项目根目录是脚本目录的上一级
PROJECT_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)

echo "Switching to project root: $PROJECT_ROOT"
cd "$PROJECT_ROOT"

# --- 设置管理员密码 ---
if [ -z "$ADMIN_PASSWORD" ]; then
    export ADMIN_PASSWORD="FbManager@StrongPass2026"
    echo "Using default ADMIN_PASSWORD"
fi

# 执行初始化/重置密码逻辑
echo "Running reset_pwd.py using uv..."
uv run python reset_pwd.py

# 启动主程序
echo "Starting application..."
# 如果在容器内运行且作为 PID 1，使用 exec 以正确处理信号
if [ -f /.dockerenv ]; then
    exec uv run python main.py
else
    uv run python main.py
fi
