@echo off
setlocal EnableExtensions
call "%~dp0restart.cmd" %*
set "EXIT_CODE=%errorlevel%"
echo.
if "%EXIT_CODE%"=="0" (
  echo [sqlGenie] Service restarted.
) else (
  echo [sqlGenie] Service restart failed with exit code %EXIT_CODE%.
)
pause
exit /b %EXIT_CODE%
