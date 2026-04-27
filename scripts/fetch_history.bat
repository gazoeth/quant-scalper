@echo off
REM Pre-warm the historical data cache for one symbol.  Usage:
REM   scripts\fetch_history.bat BTC/USDT 15m 12
setlocal
cd /d "%~dp0\.."

if not exist .venv\Scripts\activate.bat (
  echo [ERROR] No .venv yet.  Run scripts\setup.bat first.
  pause
  exit /b 1
)

call .venv\Scripts\activate.bat
set SYMBOL=%~1
set TF=%~2
set MONTHS=%~3
if "%SYMBOL%"=="" set SYMBOL=BTC/USDT
if "%TF%"==""     set TF=15m
if "%MONTHS%"=="" set MONTHS=6

python -m quant_scalper.cli fetch %SYMBOL% --timeframe %TF% --months %MONTHS%
set RC=%ERRORLEVEL%
pause
endlocal & exit /b %RC%
