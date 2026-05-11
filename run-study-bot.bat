@echo off
REM study-bot launcher.  Started by Task Scheduler at logon (see
REM docs\windows-setup.md).  The Python process loops forever, so this
REM .bat doesn't return until the user closes the window or the process
REM exits.  The window stays open if Python crashes so the operator can
REM read the traceback.

cd /d "%~dp0"

if exist .venv\Scripts\activate.bat (
  call .venv\Scripts\activate.bat
)

python -m study_bot run %*

REM Keep the window open after exit so a crash traceback can be read.
echo.
echo (study-bot exited; press any key to close)
pause >nul
