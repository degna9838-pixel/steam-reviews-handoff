@echo off
setlocal
cd /d "%~dp0"
if exist .venv\Scripts\python.exe goto check
call setup.bat
if errorlevel 1 exit /b 1
:check
.venv\Scripts\python.exe -c "import fastapi,uvicorn,httpx,langdetect,simplemma" >nul 2>&1
if errorlevel 1 call setup.bat
if errorlevel 1 exit /b 1
set "TEMP=%CD%\work"
set "TMP=%CD%\work"
set "PYTHONUTF8=1"
.venv\Scripts\python.exe -m cs2_reviews serve %*
