@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo ============================================================
echo   Campus Lost and Found - end-to-end demo
echo   (Chinese output is printed by Python below)
echo ============================================================
echo.

set "PY="
call "%~dp0tools\find_python.bat"
if not defined PY goto :end

"%PY%" run_demo.py

:end
echo.
echo Press any key to close this window.
pause >nul
