#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
APP_MODULE="${APP_MODULE:-app:app}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
RELOAD="${RELOAD:-0}"

LOG_DIR="${PROJECT_DIR}/logs"
PID_FILE="${LOG_DIR}/uvicorn.pid"
LOG_FILE="${LOG_DIR}/uvicorn.log"
ENV_FILE="${PROJECT_DIR}/.env"

mkdir -p "${LOG_DIR}"

echo "项目目录：${PROJECT_DIR}"
echo "服务地址：http://${HOST}:${PORT}"

stop_pid() {
  local pid="$1"
  if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
    echo "停止进程：${pid}"
    kill "${pid}" 2>/dev/null || true
    for _ in {1..20}; do
      if ! kill -0 "${pid}" 2>/dev/null; then
        return
      fi
      sleep 0.2
    done
    echo "进程未正常退出，强制停止：${pid}"
    kill -9 "${pid}" 2>/dev/null || true
  fi
}

if [[ -f "${PID_FILE}" ]]; then
  old_pid="$(cat "${PID_FILE}")"
  stop_pid "${old_pid}"
  rm -f "${PID_FILE}"
fi

if command -v lsof >/dev/null 2>&1; then
  port_pids="$(lsof -ti TCP:"${PORT}" || true)"
  for pid in ${port_pids}; do
    stop_pid "${pid}"
  done
fi

cd "${PROJECT_DIR}"

uvicorn_args=(
  -m uvicorn "${APP_MODULE}"
  --host "${HOST}"
  --port "${PORT}"
)

if [[ "${RELOAD}" == "1" ]]; then
  uvicorn_args+=(--reload)
fi

if [[ -f "${ENV_FILE}" ]]; then
  echo "读取环境文件：${ENV_FILE}"
  uvicorn_args+=(--env-file "${ENV_FILE}")
else
  echo "未找到 .env，将使用当前终端环境变量。"
  echo "如果企业微信 Bot 不回复，请优先确认 WECOM_* 环境变量是否已设置。"
fi

echo "启动服务，日志写入：${LOG_FILE}"
nohup "${PYTHON_BIN}" "${uvicorn_args[@]}" >>"${LOG_FILE}" 2>&1 &
new_pid="$!"
echo "${new_pid}" >"${PID_FILE}"

for _ in {1..30}; do
  if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    echo "启动成功，PID：${new_pid}"
    echo "健康检查：http://127.0.0.1:${PORT}/health"
    echo "本地调试：http://127.0.0.1:${PORT}/debug/chat"
    echo "企业微信回调：http://127.0.0.1:${PORT}/wecom/callback"
    echo "提醒：cloudflared 需要单独保持运行；如果 cloudflared 地址变了，企业微信后台 URL 也要更新。"
    exit 0
  fi
  if ! kill -0 "${new_pid}" 2>/dev/null; then
    break
  fi
  sleep 0.3
done

if kill -0 "${new_pid}" 2>/dev/null; then
  echo "启动成功，PID：${new_pid}"
  echo "但健康检查暂未通过，请查看日志：${LOG_FILE}"
else
  echo "启动失败，请查看日志：${LOG_FILE}"
  tail -n 80 "${LOG_FILE}" || true
  exit 1
fi
