@echo off
cd /d "%~dp0"
echo ==========================================
echo    EVE Toolbox - Launcher
echo ==========================================

REM --- 0) create .env from template if missing ---
if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo.
    echo [INFO] .env has been created from .env.example
    echo Open .env with Notepad and fill in YOUR OWN:
    echo    EVE_CLIENT_ID
    echo    EVE_CLIENT_SECRET
    echo Get them at https://developers.eveonline.com
    echo Then run this file again to start.
    echo.
    pause
    exit /b 1
)

REM --- 1) locate python ---
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
    echo [ERROR] Python not found.
    echo Install Python 3.12+ and tick "Add Python to PATH".
    pause
    exit /b 1
)

REM --- 2) create virtual env on first run ---
if not exist ".venv\Scripts\python.exe" (
    echo First run: creating virtual environment...
    %PY% -m venv .venv
    if errorlevel 1 ( echo Failed to create venv. & pause & exit /b 1 )
)

REM --- 3) install dependencies on first run ---
if not exist ".venv\Lib\site-packages\fastapi" (
    echo First run: installing dependencies ^(internet required^)...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 ( echo Failed to install dependencies. & pause & exit /b 1 )
)

REM --- 4) build name index if missing ---
if not exist "data\item_index.db" (
    echo Building item name index ^(first time, downloads about 170MB^)...
    ".venv\Scripts\python.exe" -m app.name_index --build
)

REM --- 5) open browser after a few seconds, then start server ---
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 4; Start-Process 'http://localhost:8000'"
echo Starting server... keep this window open.
echo If the browser did not open, visit http://localhost:8000
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
