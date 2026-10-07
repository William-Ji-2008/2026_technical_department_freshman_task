@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ============================================================
echo   Campus Lost and Found - start backend server
echo   (Chinese output is printed by Python below)
echo ============================================================
echo.

set "PY="
call "%~dp0tools\find_python.bat"
if not defined PY goto :end

echo Keep this window open while the server is running.
echo Press Ctrl + C to stop the server.
echo.
"%PY%" run_server.py

:end
echo.
echo Press any key to close this window.
pause >nul
