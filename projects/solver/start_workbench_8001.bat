@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo  EFESO Operations AI Workbench - 备用�?8001
echo  打开地址: http://127.0.0.1:8001/solver
echo  目标: E:\code\milk_logistics_editable_solver_v3
echo ============================================================
echo.

REM Kill the stale workbench proc that's squatting on 0.0.0.0:8000 (if any).
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:"0\.0\.0\.0:8000 *LISTENING"') do (
    echo [CLEAN] Killing stale workbench on 0.0.0.0:8000 (PID %%P)
    taskkill /F /PID %%P >nul 2>&1
)

REM Make sure python is on PATH or find the right interpreter.
where python >nul 2>&1
if errorlevel 1 (
    if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" (
        set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
    ) else (
        echo [ERROR] Python not on PATH. Activate a venv or install Python 3.10+.
        pause & exit /b 1
    )
) else (
    set "PY=python"
)

echo [INFO] Using Python: %PY%
echo [INFO] CWD: %CD%
echo [INFO] Ensuring dependencies...
%PY% -m pip install -r requirements.txt --disable-pip-version-check >nul 2>&1

echo.
echo [INFO] Launching uvicorn on 127.0.0.1:8001 ...
echo       (Ctrl+C to stop, this window will stay open)
echo.
%PY% -m uvicorn backend.main:app --host 127.0.0.1 --port 8001
pause
