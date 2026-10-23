@echo off
setlocal
cd /d "%~dp0"
where pyw.exe >nul 2>nul
if not errorlevel 1 (
  start "" pyw.exe -3.12 "%~dp0early_live_trial.py"
  exit /b 0
)
where py.exe >nul 2>nul
if not errorlevel 1 (
  py.exe -3.12 "%~dp0early_live_trial.py"
  exit /b %errorlevel%
)
echo Python 3.12 with requirements.txt is required for this source-only trial.
pause
