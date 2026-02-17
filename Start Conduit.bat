@echo off
setlocal enabledelayedexpansion

:: Start Conduit.bat — Double-click to start the Conduit server on Windows
::
:: This launches the AI server that powers the Conduit M4L device.
:: Keep this window open while using Conduit in Ableton.

title Conduit — AI MIDI Server

cls
echo ================================================
echo    Conduit — AI MIDI Server
echo ================================================
echo.

:: Resolve script directory
set "SCRIPT_DIR=%~dp0"
set "SERVER_DIR=%SCRIPT_DIR%server"

:: ── Check Python ────────────────────────────────────────────
set "PYTHON_CMD="
where python >nul 2>&1
if %errorlevel%==0 (
    :: Verify it's Python 3, not Python 2
    for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
    if "!PYVER:~0,1!"=="3" (
        set "PYTHON_CMD=python"
    )
)

if "%PYTHON_CMD%"=="" (
    where python3 >nul 2>&1
    if %errorlevel%==0 (
        set "PYTHON_CMD=python3"
    )
)

if "%PYTHON_CMD%"=="" (
    echo ERROR: Python 3 is not installed.
    echo.
    echo Install it from: https://www.python.org/downloads/
    echo Make sure to check "Add to PATH" during installation.
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('%PYTHON_CMD% --version 2^>^&1') do set "PYTHON_VERSION=%%v"
echo   Python:  %PYTHON_VERSION%

:: ── Check Ollama ────────────────────────────────────────────
where ollama >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo WARNING: Ollama is not installed.
    echo   Conduit needs Ollama for local AI model inference.
    echo   Install from: https://ollama.com/download
    echo.
) else (
    for /f "tokens=*" %%v in ('ollama --version 2^>^&1') do echo   Ollama:  %%v

    :: Check if llama3.2 model is available
    ollama list 2>nul | findstr /c:"llama3.2" >nul 2>&1
    if !errorlevel! neq 0 (
        echo.
        echo   Pulling llama3.2 model ^(first time only^)...
        ollama pull llama3.2
        echo.
    ) else (
        echo   Model:   llama3.2 ready
    )
)

:: ── Install Python dependencies if needed ───────────────────
%PYTHON_CMD% -c "import fastapi" >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   Installing Python dependencies...
    %PYTHON_CMD% -m pip install -q -r "%SERVER_DIR%\requirements.txt"
    if !errorlevel! neq 0 (
        echo   ERROR: Failed to install dependencies.
        echo   Try manually: pip install -r server\requirements.txt
        echo.
        pause
        exit /b 1
    )
    echo   Dependencies installed.
)

:: ── Check if server is already running ──────────────────────
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":9321.*LISTENING"') do (
    echo.
    echo   Server already running on port 9321 ^(PID %%p^).
    echo   Stopping existing server...
    taskkill /PID %%p /F >nul 2>&1
    timeout /t 1 /nobreak >nul
)

:: ── Start server ────────────────────────────────────────────
echo.
echo ================================================
echo   Starting Conduit server on http://localhost:9321
echo   Keep this window open while using Ableton.
echo   Press Ctrl+C to stop.
echo ================================================
echo.

cd /d "%SERVER_DIR%"
%PYTHON_CMD% main.py
set "STATUS=%errorlevel%"

echo.
if %STATUS% neq 0 (
    echo Server stopped with error (code %STATUS%).
) else (
    echo Server stopped.
)
echo.
pause
