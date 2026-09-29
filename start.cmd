@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Install the virtual environment and dependencies as described in README.md.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" main.py
if errorlevel 1 pause
