#!/bin/zsh
set -e
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
docker compose down
echo 'Docker 展示中心已停止；演示数据保留在数据卷中。'
