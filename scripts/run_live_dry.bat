@echo off
REM Start the live runner in dry-run mode (no orders sent).
setlocal
cd /d "%~dp0\.."

if not exist .venv\Scripts\activate.bat (
  echo [ERROR] No .venv yet.  Run scripts\setup.bat first.
  pause
  exit /b 1
)

call .venv\Scripts\activate.bat
python -m quant_scalper.cli live --dry-run
set RC=%ERRORLEVEL%
echo.
echo live --dry-run exited with code %RC%
pause
endlocal & exit /b %RC%
