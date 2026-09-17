#!/usr/bin/env bash
# 明胶配料求解系统 —— 一键启动
set -euo pipefail
cd "$(dirname "$0")"

VENV="../.venv"
PY="$VENV/bin/python"

if [ ! -x "$PY" ]; then
  echo "未找到虚拟环境 $VENV，正在创建…"
  python3 -m venv "$VENV"
  PY="$VENV/bin/python"
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q -r requirements.txt
fi

"$PY" -c "import fastapi, uvicorn, polars, scipy, fastexcel" 2>/dev/null || {
  echo "正在安装依赖…"
  "$PY" -m pip install -q -r requirements.txt
}

PORT="${PORT:-8848}"
echo ""
echo "  明胶配料求解系统"
echo "  http://127.0.0.1:${PORT}"
echo "  按 Ctrl+C 停止"
echo ""
exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
