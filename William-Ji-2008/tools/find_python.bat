@echo off
rem ============================================================
rem  Find a working Python interpreter, store it in the PY variable.
rem
rem  Usage (from another script):
rem      call "%~dp0tools\find_python.bat"
rem      if not defined PY goto :eof
rem      "%PY%" run_demo.py
rem
rem  Why actually execute it once with -c "import sys"?
rem  Because some machines have a broken "python" in PATH (a leftover
rem  virtual environment that reports "failed to locate pyvenv.cfg").
rem  Only a real run tells us whether the interpreter works.
rem ============================================================

set "PY="

rem 1) Try "python" and the "py" launcher from PATH first
for %%C in (python py) do (
    if not defined PY (
        %%C -c "import sys" >nul 2>nul && set "PY=%%C"
    )
)

rem 2) Then look in the usual install locations
if not defined PY (
    for %%P in (
        "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
        "C:\Python314\python.exe"
        "C:\Python313\python.exe"
        "C:\Python312\python.exe"
        "D:\Python314\python.exe"
        "D:\hclaw\python\python.exe"
    ) do (
        if not defined PY (
            if exist %%P "%%~fP" -c "import sys" >nul 2>nul && set "PY=%%~fP"
        )
    )
)

if not defined PY (
    echo.
    echo [ERROR] No usable Python interpreter was found.
    echo         Please install Python 3.9 or newer and tick
    echo         "Add python.exe to PATH" during setup.
    echo         Download: https://www.python.org/downloads/
    echo.
    exit /b 1
)
exit /b 0
