@echo off
REM Start the live runner in dry-run mode (no orders sent).
cd /d "%~dp0\.."
call .venv\Scripts\activate.bat
python -m quant_scalper.cli live --dry-run
