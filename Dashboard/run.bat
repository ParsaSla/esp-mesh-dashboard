@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv .venv
    ) else (
        where python >nul 2>nul
        if errorlevel 1 (
            echo Python was not found. Install Python 3 and add it to PATH.
            exit /b 1
        )
        python -m venv .venv
    )
    if errorlevel 1 (
        echo Failed to create the virtual environment.
        pause
        exit /b 1
    )
)

".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install the required packages.
    pause
    exit /b 1
)

echo Starting ESP32 Mesh Dashboard at http://localhost:8000
".venv\Scripts\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8000
if errorlevel 1 (
    echo The dashboard server exited with an error.
    pause
)
