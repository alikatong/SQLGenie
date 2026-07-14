@echo off
setlocal EnableExtensions
call "%~dp0sqlgenie_runtime.cmd" export || exit /b 1
cd /d "%SQLGENIE_ROOT%"
set "PYTHONPATH=%PYTHONPATH_VALUE%"
set "PYTHONIOENCODING=utf-8"
"%PYTHON_EXE%" scripts\run_backend.py 1>> backend.log 2>> backend.err.log
