@echo off
setlocal EnableExtensions
call "%~dp0scripts\sqlgenie_runtime.cmd" export || exit /b 1
cd /d "%SQLGENIE_ROOT%"

echo [sqlGenie] Rebuilding project...

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

if not exist "%NODE_EXE%" (
  echo [sqlGenie] Missing Node runtime: %NODE_EXE%
  exit /b 1
)

if not exist "%PYTHON_PACKAGES%" (
  echo [sqlGenie] Installing backend dependencies...
  "%PYTHON_EXE%" -m pip install -r backend\requirements.txt --target .python_packages
  if errorlevel 1 (
    echo [sqlGenie] Backend dependency installation failed.
    exit /b 1
  )
)

if not exist "%FRONTEND_DIR%\node_modules" (
  if not exist "%PNPM_EXE%" (
    echo [sqlGenie] Missing pnpm runtime: %PNPM_EXE%
    exit /b 1
  )

  echo [sqlGenie] Installing frontend dependencies...
  pushd "%FRONTEND_DIR%"
  call "%PNPM_EXE%" install --frozen-lockfile
  set "INSTALL_EXIT=%errorlevel%"
  popd
  if not "%INSTALL_EXIT%"=="0" (
    echo [sqlGenie] Frontend dependency installation failed.
    exit /b %INSTALL_EXIT%
  )
)

echo [sqlGenie] Compiling backend...
set "PYTHONPATH=%PYTHONPATH_VALUE%"
set "PYTHONIOENCODING=utf-8"
"%PYTHON_EXE%" -m compileall backend
if errorlevel 1 (
  echo [sqlGenie] Backend compilation failed.
  exit /b 1
)

echo [sqlGenie] Building frontend...
pushd "%FRONTEND_DIR%"
"%NODE_EXE%" "%VITE_BIN%" build
set "BUILD_EXIT=%errorlevel%"
popd
if not "%BUILD_EXIT%"=="0" (
  echo [sqlGenie] Frontend build failed.
  exit /b %BUILD_EXIT%
)

echo [sqlGenie] Rebuild completed.
exit /b 0
