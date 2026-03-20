#!/bin/bash
# 自动化 Docker 构建和启动脚本

# 确保在出错时立即退出
set -e

# 获取脚本所在的目录，并切换到脚本目录
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR"

echo "Step 1: Building Docker image..."
docker-compose build

echo "Step 2: Starting Docker container in background..."
docker-compose up -d

echo "Step 3: Showing container status..."
docker-compose ps

echo "Success! The application is now running in Docker container."
echo "You can view logs with: docker-compose logs -f"
