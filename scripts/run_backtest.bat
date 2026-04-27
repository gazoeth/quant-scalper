@echo off
REM Run the full multi-symbol 12-month backtest.  Reports go to data\reports\.
setlocal
cd /d "%~dp0\.."

if not exist .venv\Scripts\activate.bat (
  echo [ERROR] No .venv yet.  Run scripts\setup.bat first.
  pause
  exit /b 1
)

call .venv\Scripts\activate.bat
python -m quant_scalper.cli backtest --top 26 --months 12 ^
  --report data\reports\run.html ^
  --csv    data\reports\run.csv
set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (
  echo Report: %CD%\data\reports\run.html
  echo Trades: %CD%\data\reports\run.csv
) else (
  echo [ERROR] backtest exited with code %RC%
)
pause
endlocal & exit /b %RC%
