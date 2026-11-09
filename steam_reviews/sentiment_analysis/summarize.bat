@echo off
setlocal
cd /d "%~dp0.."
if not exist .venv\Scripts\python.exe (
  echo Run setup.bat first.
  exit /b 1
)
.venv\Scripts\python.exe -m sentiment_analysis summarize %*
