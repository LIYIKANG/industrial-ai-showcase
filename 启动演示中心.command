#!/bin/zsh
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -c 'import fastapi, streamlit, scipy, anthropic, polars, pyjobshop' >/dev/null 2>&1; then
  echo '正在准备运行环境，首次启动需要联网安装依赖……'
  PYTHON_BIN=""
  for candidate in python3.12 python3.11 python3.13 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; assert (3,10) <= sys.version_info[:2] < (3,14)' >/dev/null 2>&1; then
        PYTHON_BIN="$candidate"
        break
      fi
    fi
  done
  if [[ -z "$PYTHON_BIN" ]]; then
    echo '请先安装 Python 3.12，然后重新双击此文件。'
    read '?按回车退出…'
    exit 1
  fi
  "$PYTHON_BIN" -m venv .venv || exit 1
  .venv/bin/python -m pip install -r requirements-lock.txt || { read '?安装未完成，按回车退出…'; exit 1; }
fi
.venv/bin/python launcher.py
if [[ $? -ne 0 ]]; then
  read '?启动出现问题，请查看上方提示。按回车退出…'
fi
