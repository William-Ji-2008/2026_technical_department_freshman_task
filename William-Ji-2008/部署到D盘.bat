@echo off
chcp 65001 >nul

set "SOURCE=%~dp0"
set "TARGET=D:\campus_lost_found_local"

echo.
echo ============================================================
echo   Copy this project to the D drive
echo ============================================================
echo Source: %SOURCE%
echo Target: %TARGET%
echo.

rem /E  copy subdirectories, including empty ones
rem /XD exclude "data" (the database) and "__pycache__" (Python cache)
rem robocopy exit codes 0..7 mean success; 8 or above means an error
robocopy "%SOURCE%." "%TARGET%" /E /XD data __pycache__ /NFL /NDL /NJH /NJS >nul

if %ERRORLEVEL% LEQ 7 (
    echo Done. The project has been copied to %TARGET%
    echo.
    echo Next: open that folder and double-click "kong-zhi-tai.bat" style launchers,
    echo       i.e. the .bat files next to README.md.
) else (
    echo Copy failed. robocopy exit code: %ERRORLEVEL%
    echo Common causes: D drive missing, no write permission, target in use.
)

echo.
echo Press any key to close this window.
pause >nul
