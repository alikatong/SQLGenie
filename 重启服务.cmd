@echo off
call "%~dp0restart.cmd" %*
exit /b %errorlevel%
