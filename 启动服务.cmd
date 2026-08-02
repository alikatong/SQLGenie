@echo off
setlocal EnableExtensions
wscript.exe "%~dp0scripts\start_service_hidden.vbs"
exit /b %errorlevel%
