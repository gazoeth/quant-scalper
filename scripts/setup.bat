@echo off
REM ============================================================
REM   quant-scalper Windows setup script.
REM   Creates a venv, installs dependencies, runs the test suite.
REM   Always pauses on failure so you can read the error.
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0\.."

echo ============================================================
echo  quant-scalper setup
echo  cwd: %CD%
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python is not on PATH.
  echo         Install Python 3.10+ from https://www.python.org and
  echo         tick "Add Python to PATH" during install.
  goto :end_fail
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo Python: %%v
for /f "tokens=*" %%v in ('where python') do echo            %%v

if not exist .venv (
  echo.
  echo Creating virtual environment in .venv ...
  python -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Failed to create venv.
    goto :end_fail
  )
)

call .venv\Scripts\activate.bat
echo Active interpreter:
where python
echo.

python -m pip install --upgrade pip
if errorlevel 1 ( echo [ERROR] pip self-upgrade failed. & goto :end_fail )

echo.
echo Installing requirements-dev.txt ...
pip install -r requirements-dev.txt
if errorlevel 1 (
  echo [ERROR] pip install failed.  See above output for the failing package.
  goto :end_fail
)

if not exist .env (
  copy /Y .env.example .env >nul
  echo Created .env from .env.example.  Edit it to add your Binance keys.
)

echo.
echo Running tests ...
pytest -q
if errorlevel 1 (
  echo [WARN] Tests failed; setup is otherwise complete.
)

echo.
echo ============================================================
echo  Setup complete.
echo  Next: edit .env, then double-click scripts\check_connectivity.bat
echo ============================================================
set RC=0
goto :end

:end_fail
set RC=1
:end
echo.
pause
endlocal & exit /b %RC%
