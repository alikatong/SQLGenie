@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "SQLGENIE_ROOT=%%~fI"

if not defined APP_HOST set "APP_HOST=127.0.0.1"
if not defined APP_PORT set "APP_PORT=8000"
if not defined VITE_PORT set "VITE_PORT=5173"

set "PYTHON_EXE=C:\Users\T14P\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
set "NODE_EXE=C:\Users\T14P\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
set "PNPM_EXE=C:\Users\T14P\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\pnpm.cmd"

set "PYTHON_PACKAGES=%SQLGENIE_ROOT%\.python_packages"
set "PYTHONPATH_VALUE=%PYTHON_PACKAGES%"
set "FRONTEND_DIR=%SQLGENIE_ROOT%\frontend"
set "FRONTEND_DIST_INDEX=%FRONTEND_DIR%\dist\index.html"
set "VITE_BIN=%FRONTEND_DIR%\node_modules\vite\bin\vite.js"
set "HEALTH_URL=http://127.0.0.1:%APP_PORT%/api/health"
set "HEALTH_CHECKER=%SQLGENIE_ROOT%\scripts\wait_for_health.py"
set "BACKEND_STARTER=%SQLGENIE_ROOT%\scripts\start_backend.cmd"
set "BACKEND_STARTER_VBS=%SQLGENIE_ROOT%\scripts\start_backend_hidden.vbs"
set "ENV_FILE=%SQLGENIE_ROOT%\.env"
set "ENV_EXAMPLE_FILE=%SQLGENIE_ROOT%\.env.example"

if /i "%~1"=="export" (
  endlocal & (
    set "SQLGENIE_ROOT=%SQLGENIE_ROOT%"
    set "APP_HOST=%APP_HOST%"
    set "APP_PORT=%APP_PORT%"
    set "VITE_PORT=%VITE_PORT%"
    set "PYTHON_EXE=%PYTHON_EXE%"
    set "NODE_EXE=%NODE_EXE%"
    set "PNPM_EXE=%PNPM_EXE%"
    set "PYTHON_PACKAGES=%PYTHON_PACKAGES%"
    set "PYTHONPATH_VALUE=%PYTHONPATH_VALUE%"
    set "FRONTEND_DIR=%FRONTEND_DIR%"
    set "FRONTEND_DIST_INDEX=%FRONTEND_DIST_INDEX%"
    set "VITE_BIN=%VITE_BIN%"
    set "HEALTH_URL=%HEALTH_URL%"
    set "HEALTH_CHECKER=%HEALTH_CHECKER%"
    set "BACKEND_STARTER=%BACKEND_STARTER%"
    set "BACKEND_STARTER_VBS=%BACKEND_STARTER_VBS%"
    set "ENV_FILE=%ENV_FILE%"
    set "ENV_EXAMPLE_FILE=%ENV_EXAMPLE_FILE%"
  )
  exit /b 0
)

endlocal
exit /b 0
