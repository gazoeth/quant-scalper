@echo off
REM Pre-warm the historical data cache for one symbol.  Usage:
REM   scripts\fetch_history.bat BTC/USDT 15m 12
cd /d "%~dp0\.."
call .venv\Scripts\activate.bat
set SYMBOL=%1
set TF=%2
set MONTHS=%3
if "%SYMBOL%"=="" set SYMBOL=BTC/USDT
if "%TF%"=="" set TF=15m
if "%MONTHS%"=="" set MONTHS=6
python -m quant_scalper.cli fetch %SYMBOL% --timeframe %TF% --months %MONTHS%
pause
