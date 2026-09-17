@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist outputs mkdir outputs
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 > outputs\uvicorn.live.log 2>&1
