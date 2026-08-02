@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

IF NOT EXIST ".venv\Scripts\activate" (
    echo [OS GOD] No virtual environment found. Creating one...
    python -m venv .venv
    if errorlevel 1 (
        echo [OS GOD] Failed to create the virtual environment.
        pause
        exit /b 1
    )
    call .venv\Scripts\activate
    echo [OS GOD] Installing dependencies...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [OS GOD] Dependency installation failed. Review the error above and try again.
        pause
        exit /b 1
    )
) ELSE (
    call .venv\Scripts\activate
)

echo [OS GOD] Starting core engine in background...
start "OS GOD Core" ".venv\Scripts\python.exe" main.py

echo [OS GOD] Waiting for OS GOD to be ready...
set /a attempts=0
:WAIT
timeout /t 1 /nobreak >nul
curl -s http://localhost:5000/ping >nul 2>&1
if not errorlevel 1 goto READY
set /a attempts+=1
if !attempts! lss 30 goto WAIT

echo [OS GOD] The core did not start within 30 seconds.
echo [OS GOD] Check the "OS GOD Core" window for the actual error.
pause
exit /b 1

:READY
echo [OS GOD] Online. Launching JARVIS Command Center...
".venv\Scripts\python.exe" launch_dashboard.py

pause
