@echo off
setlocal
cd /d "%~dp0"
if not exist work mkdir work
if not exist data mkdir data
if not exist logs mkdir logs
set "TEMP=%CD%\work"
set "TMP=%CD%\work"
set "PIP_CACHE_DIR=%CD%\work\pip-cache"
set "PYTHONUTF8=1"
if exist .venv\Scripts\python.exe goto install
set "PYTHON=py -3.12"
%PYTHON% -c "import sys; assert (3,11) <= sys.version_info[:2] < (3,15)" >nul 2>&1
if not errorlevel 1 goto create
set "PYTHON=py -3"
%PYTHON% -c "import sys; assert (3,11) <= sys.version_info[:2] < (3,15)" >nul 2>&1
if not errorlevel 1 goto create
set "PYTHON=python"
%PYTHON% -c "import sys; assert (3,11) <= sys.version_info[:2] < (3,15)" >nul 2>&1
if not errorlevel 1 goto create
echo Please install Python 3.12 from https://www.python.org/downloads/ and enable the Python launcher.
exit /b 1
:create
%PYTHON% -m venv .venv
if errorlevel 1 exit /b 1
:install
.venv\Scripts\python.exe -c "import sys; assert (3,11) <= sys.version_info[:2] < (3,15), 'Python 3.11-3.14 required'"
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip check
if errorlevel 1 exit /b 1
echo Ready. Run start.bat or start.bat --sample
exit /b 0
