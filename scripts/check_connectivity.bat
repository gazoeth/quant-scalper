@echo off
REM ============================================================
REM   Verify connectivity to Binance USDT-M testnet end-to-end.
REM   Always pauses at the end so you can read the output, even
REM   when something fails.
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0\.."

echo ============================================================
echo  quant-scalper connectivity check
echo  cwd: %CD%
echo ============================================================
echo.

REM --- Python on PATH? ---------------------------------------
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python is not on PATH.
  echo         Re-install Python 3.10+ from https://www.python.org and
  echo         tick "Add Python to PATH" during install, then re-open
  echo         this command window so PATH refreshes.
  goto :end_fail
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo Python: %%v

REM --- venv exists? ------------------------------------------
if not exist .venv\Scripts\activate.bat (
  echo.
  echo [ERROR] No virtual env found at .venv\
  echo         You probably haven't run scripts\setup.bat yet.
  echo         I can run setup for you now ^(creates .venv, installs deps^).
  set /p ANS="Run setup.bat now? [Y/N] "
  if /i "!ANS!"=="Y" (
    call "%~dp0setup.bat"
    if errorlevel 1 (
      echo.
      echo [ERROR] setup.bat failed.  Read its output above for the cause.
      goto :end_fail
    )
  ) else (
    goto :end_fail
  )
)

REM --- .env present? ------------------------------------------
if not exist .env (
  echo.
  echo [WARN] No .env file found.  Copying .env.example to .env ...
  copy /Y .env.example .env >nul
  echo        Edit .env now to add BINANCE_API_KEY / BINANCE_API_SECRET
  echo        ^(testnet keys^) and optionally QS_PROXY_URL, then re-run
  echo        this script.
  goto :end_fail
)

REM --- Activate venv & run check -----------------------------
call .venv\Scripts\activate.bat
echo Using interpreter:
where python
echo.
python -m quant_scalper.tools.connectivity_check
set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (
  echo ============================================================
  echo  Connectivity OK.  Safe to start the live trader on testnet.
  echo ============================================================
) else (
  echo ============================================================
  echo  Connectivity check FAILED with code %RC%.
  echo  Fix the issue above before running the bot.
  echo ============================================================
)
goto :end

:end_fail
set RC=1
:end
echo.
pause
endlocal & exit /b %RC%
