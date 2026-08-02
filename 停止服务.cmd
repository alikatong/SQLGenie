@echo off
setlocal EnableExtensions
call "%~dp0stop.cmd" %*
set "EXIT_CODE=%errorlevel%"
echo.
if "%EXIT_CODE%"=="0" (
  echo [sqlGenie] Service stopped.
) else (
  echo [sqlGenie] Service stop failed with exit code %EXIT_CODE%.
)
pause
exit /b %EXIT_CODE%
