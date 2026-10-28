@echo off
setlocal DisableDelayedExpansion
if not exist "%~dp0..\python-3.11.9-amd64\python.exe" (
    echo Error: Env's bundled Python was not found. 1>&2
    exit /b 1
)
"%~dp0..\python-3.11.9-amd64\python.exe" -X utf8 "%~dp0format-changed.py" %*
exit /b %errorlevel%
