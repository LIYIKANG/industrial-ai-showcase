#!/bin/zsh
set -e
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if ! docker info >/dev/null 2>&1; then
  echo 'Docker 引擎未启动。请启动 Docker Desktop，或执行 colima start 后重试。'
  read '?按回车退出…'
  exit 1
fi
docker compose up -d --build --wait --wait-timeout 240
open 'http://127.0.0.1:18080'
echo 'Docker 展示中心已启动：http://127.0.0.1:18080'
