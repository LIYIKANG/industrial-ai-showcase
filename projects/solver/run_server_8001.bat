@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo ============================================================
echo  EFESO Operations AI Workbench - 备用端口
echo  打开地址: http://127.0.0.1:8001/solver
echo  如果 8000 被旧进程占用，请先用这个地址访问。
echo ============================================================
echo.
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001
pause
