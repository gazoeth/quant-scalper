@echo off
REM ============================================================
REM   Verify connectivity to Binance USDT-M testnet end-to-end:
REM     1. Local proxy port reachable (if QS_PROXY_URL set)
REM     2. Public testnet endpoint (server time + market list)
REM     3. Authenticated calls (balance, positions)
REM     4. Round-trip limit order (place + cancel)
REM   Run this BEFORE starting the live trader for the first time.
REM ============================================================
setlocal
cd /d "%~dp0\.."
if not exist .venv\Scripts\activate.bat (
  echo [ERROR] No virtual env found.  Run scripts\setup.bat first.
  exit /b 1
)
call .venv\Scripts\activate.bat
python -m quant_scalper.tools.connectivity_check
set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (
  echo Connectivity OK.  Safe to start the live trader.
) else (
  echo Connectivity check FAILED with code %RC%.  Fix the issue above before running the bot.
)
pause
endlocal & exit /b %RC%
