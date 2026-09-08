@echo off
cd /d "%~dp0"

set "SCRIPT=%~dp0codex_retry_gui.pyw"
set "PYW=%LocalAppData%\Programs\Python\Python312\pythonw.exe"

if exist "%PYW%" goto launch
where pythonw >nul 2>&1
if %errorlevel%==0 (
  set "PYW=pythonw"
  goto launch
)

echo pythonw.exe not found.
echo Install Python 3.10+ and enable Add python.exe to PATH.
pause
exit /b 1

:launch
start "CodexRetry" "%PYW%" "%SCRIPT%"
exit /b 0
