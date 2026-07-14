@echo off
setlocal EnableExtensions
call "%~dp0scripts\sqlgenie_runtime.cmd" export || exit /b 1
cd /d "%SQLGENIE_ROOT%"

set "OPEN_BROWSER=1"
if /i "%~1"=="--no-browser" set "OPEN_BROWSER=0"
if /i "%~1"=="/nobrowser" set "OPEN_BROWSER=0"

echo [sqlGenie] Starting service...

if not exist "%ENV_FILE%" (
  if exist "%ENV_EXAMPLE_FILE%" (
    copy /Y "%ENV_EXAMPLE_FILE%" "%ENV_FILE%" >nul
    echo [sqlGenie] Created .env from .env.example
  ) else (
    echo [sqlGenie] Missing .env.example
    exit /b 1
  )
)

if not exist "%PYTHON_EXE%" (
  echo [sqlGenie] Missing Python runtime: %PYTHON_EXE%
  exit /b 1
)

if not exist "%BACKEND_STARTER_VBS%" (
  echo [sqlGenie] Missing %BACKEND_STARTER_VBS%
  exit /b 1
)

if not exist ".python_packages" set "NEEDS_REBUILD=1"
if not exist "%FRONTEND_DIST_INDEX%" set "NEEDS_REBUILD=1"

if defined NEEDS_REBUILD (
  echo [sqlGenie] Build output is missing, rebuilding first...
  call "%SQLGENIE_ROOT%\build.cmd"
  if errorlevel 1 (
    echo [sqlGenie] Rebuild failed.
    exit /b 1
  )
)

"%PYTHON_EXE%" "%HEALTH_CHECKER%" "%HEALTH_URL%" 1 0 >nul 2>nul
if not errorlevel 1 (
  echo [sqlGenie] Service already running on port %APP_PORT%.
  if "%OPEN_BROWSER%"=="1" powershell -NoProfile -Command "Start-Process 'http://127.0.0.1:%APP_PORT%/'"
  exit /b 0
)

wscript.exe "%BACKEND_STARTER_VBS%"

"%PYTHON_EXE%" "%HEALTH_CHECKER%" "%HEALTH_URL%" 40 0.5

if errorlevel 1 (
  echo [sqlGenie] Start failed, check backend.err.log
  exit /b 1
)

echo [sqlGenie] Service started.
echo [sqlGenie] Local Home: http://127.0.0.1:%APP_PORT%/
echo [sqlGenie] Local Docs: http://127.0.0.1:%APP_PORT%/docs
echo [sqlGenie] LAN access: http://YOUR_COMPUTER_IP:%APP_PORT%/
if "%OPEN_BROWSER%"=="1" powershell -NoProfile -Command "Start-Process 'http://127.0.0.1:%APP_PORT%/'"
exit /b 0
