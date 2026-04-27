@echo off
REM Run the full multi-symbol 12-month backtest.  Reports go to data\reports\.
cd /d "%~dp0\.."
call .venv\Scripts\activate.bat
python -m quant_scalper.cli backtest --top 26 --months 12 ^
  --report data\reports\run.html ^
  --csv    data\reports\run.csv
echo.
echo Report: data\reports\run.html
echo Trades: data\reports\run.csv
pause
