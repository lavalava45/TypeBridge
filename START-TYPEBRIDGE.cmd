@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0dist\TypeBridge.exe" (
  start "" "%~dp0dist\TypeBridge.exe"
  exit /b 0
)
where pyw.exe >nul 2>nul
if not errorlevel 1 (
  start "" pyw.exe -3.12 "%~dp0typebridge_app.py"
  exit /b 0
)
where py.exe >nul 2>nul
if not errorlevel 1 (
  py.exe -3.12 "%~dp0typebridge_app.py"
  exit /b %errorlevel%
)
echo Python 3.12 was not found.
pause
exit /b 1
