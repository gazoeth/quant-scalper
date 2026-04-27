@echo off
REM Start the live runner against the configured Binance account.
REM Make sure QS_MODE=testnet (recommended) or QS_MODE=live in .env.
setlocal
cd /d "%~dp0\.."

if not exist .venv\Scripts\activate.bat (
  echo [ERROR] No .venv yet.  Run scripts\setup.bat first.
  pause
  exit /b 1
)

call .venv\Scripts\activate.bat
python -m quant_scalper.cli live --real
set RC=%ERRORLEVEL%
echo.
echo live --real exited with code %RC%
pause
endlocal & exit /b %RC%
