#!/bin/zsh
# 任何命令失败时立即停止，避免在依赖不完整时继续启动。
set -e

# 无论从哪里双击或执行，都先进入本项目所在目录。
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "=============================================="
echo " 明胶分子量复配计算与 AI 辅助决策 Demo"
echo "=============================================="

if ! command -v python3 >/dev/null 2>&1; then
    echo "未找到 Python 3，请先安装 Python 3。"
    echo "下载地址：https://www.python.org/downloads/"
    read "?按回车键退出..."
    exit 1
fi

# 首次运行时创建项目独立环境，避免污染系统 Python。
if [ ! -d ".venv" ]; then
    echo "首次启动：正在创建 Python 独立环境..."
    python3 -m venv .venv
fi

source .venv/bin/activate

echo "正在检查并安装项目依赖..."
python3 -m pip install --disable-pip-version-check -r requirements.txt

echo ""
echo "启动成功后，浏览器将打开：http://localhost:8501"
echo "停止程序请在本窗口按 Control + C"
echo ""

python3 -m streamlit run app.py
