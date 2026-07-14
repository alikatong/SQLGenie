@echo off
call "%~dp0stop.cmd" %*
exit /b %errorlevel%
