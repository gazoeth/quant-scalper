@echo off
REM ============================================================
REM   quant-scalper Windows setup script
REM   Creates a venv, installs dependencies, runs the test suite.
REM ============================================================
setlocal enabledelayedexpansion

cd /d "%~dp0\.."

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python is not on PATH. Install Python 3.10+ from https://www.python.org and tick "Add to PATH" during install.
  exit /b 1
)

if not exist .venv (
  echo Creating virtual environment in .venv ...
  python -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Failed to create venv.
    exit /b 1
  )
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
if errorlevel 1 (
  echo [ERROR] pip install failed.
  exit /b 1
)

if not exist .env (
  copy /Y .env.example .env >nul
  echo Created .env from .env.example.  Edit it to add your Binance keys.
)

echo.
echo Running tests ...
pytest -q
echo.
echo Setup complete.  Activate the venv any time with:  .venv\Scripts\activate.bat
endlocal
