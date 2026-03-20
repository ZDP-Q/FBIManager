#!/bin/bash
# 自动化 Docker 构建和部署脚本

# 确保在出错时立即退出
set -e

# 获取脚本所在的目录，并切换到项目根目录
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
PROJECT_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$PROJECT_ROOT"

# 注意：docker compose 在 v2 之后不需要中间的连字符
echo "Step 1: Building Docker image..."
docker compose -f docker/docker-compose.yml build

echo "Step 2: Starting Docker container in background..."
docker compose -f docker/docker-compose.yml up -d

echo "Step 3: Showing container status..."
docker compose -f docker/docker-compose.yml ps

echo "Success! The application is now running in Docker container."
echo "You can view logs with: docker compose -f docker/docker-compose.yml logs -f"
