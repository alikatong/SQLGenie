@echo off
setlocal EnableExtensions
call "%~dp0scripts\sqlgenie_runtime.cmd" export || exit /b 1
cd /d "%~dp0"

set "SHOULD_REBUILD=0"
set "FORWARD_START_ARG="
if /i "%~1"=="rebuild" set "SHOULD_REBUILD=1"
if /i "%~1"=="--rebuild" set "SHOULD_REBUILD=1"
if /i "%~1"=="--no-browser" set "FORWARD_START_ARG=--no-browser"
if /i "%~2"=="--no-browser" set "FORWARD_START_ARG=--no-browser"

echo [sqlGenie] Restarting service...
call "%~dp0stop.cmd"
if errorlevel 1 exit /b %errorlevel%

if "%SHOULD_REBUILD%"=="1" (
  call "%~dp0build.cmd"
  if errorlevel 1 exit /b %errorlevel%
)

powershell -NoProfile -Command "Start-Sleep -Seconds 2"
wscript.exe "%BACKEND_STARTER_VBS%"
"%PYTHON_EXE%" "%HEALTH_CHECKER%" "%HEALTH_URL%" 40 0.5
if errorlevel 1 (
  echo [sqlGenie] Restart failed, check backend.err.log
  exit /b 1
)

echo [sqlGenie] Service restarted.
echo [sqlGenie] Local Home: http://127.0.0.1:%APP_PORT%/
echo [sqlGenie] Local Docs: http://127.0.0.1:%APP_PORT%/docs
echo [sqlGenie] LAN access: http://YOUR_COMPUTER_IP:%APP_PORT%/
if not "%FORWARD_START_ARG%"=="--no-browser" powershell -NoProfile -Command "Start-Process 'http://127.0.0.1:%APP_PORT%/'"
exit /b 0
