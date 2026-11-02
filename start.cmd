@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto usepython
py -3 --version >nul 2>nul
if errorlevel 1 goto usepython
set "DISPLAY_PY=py -3"
goto run
:usepython
where python >nul 2>nul
if errorlevel 1 goto missing
set "DISPLAY_PY=python"
:run
if not exist config.local.json %DISPLAY_PY% configure.py
if not exist config.local.json goto failed
%DISPLAY_PY% build_live.py
if errorlevel 1 goto failed
%DISPLAY_PY% server.py --open
if errorlevel 1 goto failed
exit /b 0
:missing
echo Python 3.10+ is required. Install Python from python.org first.
:failed
pause
exit /b 1
