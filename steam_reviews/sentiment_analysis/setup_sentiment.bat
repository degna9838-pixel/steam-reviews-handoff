@echo off
setlocal
cd /d "%~dp0.."
if not exist .venv\Scripts\python.exe (
  echo Run setup.bat first.
  exit /b 1
)
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install -r sentiment_analysis\requirements-sentiment.txt
if errorlevel 1 exit /b 1
echo Sentiment dependencies ready.
