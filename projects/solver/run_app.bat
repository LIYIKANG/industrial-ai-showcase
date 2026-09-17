@echo off
chcp 65001 >nul
echo.
echo ============================================================
echo  EFESO Operations AI Workbench
echo  AI建模 -> 人工校验 -> 本地HiGHS严格求解
echo  打开地址: http://127.0.0.1:8000/solver
echo ============================================================
echo.
python -m pip install -r requirements.txt
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
pause
