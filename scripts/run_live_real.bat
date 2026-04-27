@echo off
REM Start the live runner against the configured Binance account.
REM Make sure QS_MODE=testnet (recommended) or QS_MODE=live in .env.
cd /d "%~dp0\.."
call .venv\Scripts\activate.bat
python -m quant_scalper.cli live --real
