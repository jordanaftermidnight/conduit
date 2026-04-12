@echo off
setlocal enabledelayedexpansion

:: Install Conduit.bat — One-click installer for Conduit (Windows)
::
:: Double-click this file to install Conduit. You only need to do this once.
:: After installing, just open Ableton and drag Conduit onto a MIDI track.

title Conduit — Installer

cls
echo ================================================
echo    Conduit — Installer
echo ================================================
echo.

:: Resolve script directory
set "SCRIPT_DIR=%~dp0"
set "SERVER_SRC=%SCRIPT_DIR%server"

:: Install destinations — detect OneDrive-redirected Documents folder
set "DOCS_DIR=%USERPROFILE%\Documents"
if exist "%USERPROFILE%\OneDrive\Documents\Ableton" (
    set "DOCS_DIR=%USERPROFILE%\OneDrive\Documents"
)
if exist "%USERPROFILE%\OneDrive - *\Documents\Ableton" (
    for /d %%d in ("%USERPROFILE%\OneDrive - *") do (
        if exist "%%~d\Documents\Ableton" set "DOCS_DIR=%%~d\Documents"
    )
)

set "CONDUIT_HOME=%DOCS_DIR%\Conduit"
set "SERVER_DST=%CONDUIT_HOME%\server"

:: Ableton User Library path (Windows)
set "ABLETON_MIDI_FX=%DOCS_DIR%\Ableton\User Library\Presets\MIDI Effects\Max MIDI Effect"

:: Max Packages — check both standard and OneDrive paths
set "MAX8_PKG=%DOCS_DIR%\Max 8\Packages\Conduit\javascript"
set "MAX9_PKG=%DOCS_DIR%\Max 9\Packages\Conduit\javascript"

:: ── [1/5] Check Python 3.9+ ─────────────────────────────────
echo [1/5] Checking Python...

set "PYTHON_CMD="
where python >nul 2>&1
if %errorlevel%==0 (
    for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
    if "!PYVER:~0,1!"=="3" (
        set "PYTHON_CMD=python"
    )
)

if "!PYTHON_CMD!"=="" (
    where python3 >nul 2>&1
    if !errorlevel!==0 (
        for /f "tokens=2 delims= " %%v in ('python3 --version 2^>^&1') do set "PYVER=%%v"
        if "!PYVER:~0,1!"=="3" (
            set "PYTHON_CMD=python3"
        )
    )
)

if "!PYTHON_CMD!"=="" (
    echo.
    echo   ERROR: Python 3 is not installed.
    echo   Install from: https://www.python.org/downloads/
    echo   Make sure to check "Add to PATH" during installation.
    echo.
    pause
    exit /b 1
)

:: Check version >= 3.9
for /f "tokens=1,2 delims=." %%a in ("!PYVER!") do (
    set "PY_MAJOR=%%a"
    set "PY_MINOR=%%b"
)

if !PY_MAJOR! lss 3 (
    echo   ERROR: Python !PYVER! is too old. Conduit requires Python 3.9+.
    pause
    exit /b 1
)
if !PY_MAJOR!==3 if !PY_MINOR! lss 9 (
    echo   ERROR: Python !PYVER! is too old. Conduit requires Python 3.9+.
    pause
    exit /b 1
)

echo   Python !PYVER! OK

:: ── [2/5] Check Ollama ───────────────────────────────────────
echo.
echo [2/5] Checking Ollama...

where ollama >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   ERROR: Ollama is not installed.
    echo   Conduit needs Ollama for local AI model inference.
    echo.
    echo   Install from: https://ollama.com/download
    echo   Then re-run this installer.
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%v in ('ollama --version 2^>^&1') do echo   Ollama installed: %%v

:: Check if Ollama server is reachable
!PYTHON_CMD! -c "import urllib.request; urllib.request.urlopen('http://localhost:11434', timeout=3)" >nul 2>&1
if %errorlevel% neq 0 (
    echo   Ollama server not running — attempting to start...
    start /b ollama serve >nul 2>&1
    timeout /t 3 /nobreak >nul
    !PYTHON_CMD! -c "import urllib.request; urllib.request.urlopen('http://localhost:11434', timeout=3)" >nul 2>&1
    if !errorlevel! neq 0 (
        echo.
        echo   WARNING: Could not start Ollama server.
        echo   Open the Ollama app manually, then re-run this installer.
        echo.
        pause
        exit /b 1
    )
)
echo   Ollama server reachable

:: ── [3/5] Pull llama3.2 model ────────────────────────────────
echo.
echo [3/5] Checking model...

ollama list 2>nul | findstr /c:"llama3.2" >nul 2>&1
if !errorlevel! neq 0 (
    echo   Downloading llama3.2 ^(~2GB, first time only^)...
    ollama pull llama3.2
    echo   llama3.2 downloaded
) else (
    echo   llama3.2 already downloaded
)

:: ── [4/5] Install Python dependencies ────────────────────────
echo.
echo [4/5] Installing Python dependencies...

!PYTHON_CMD! -c "import fastapi" >nul 2>&1
if %errorlevel% neq 0 (
    !PYTHON_CMD! -m pip install -q -r "%SERVER_SRC%\requirements.txt"
    if !errorlevel! neq 0 (
        echo.
        echo   ERROR: Failed to install Python dependencies.
        echo   Try manually: pip install -r server\requirements.txt
        echo.
        pause
        exit /b 1
    )
    echo   Dependencies installed
) else (
    echo   Dependencies already installed
)

:: ── [5/5] Install files ──────────────────────────────────────
echo.
echo [5/5] Installing files...

:: Copy server files
if not exist "%SERVER_DST%" mkdir "%SERVER_DST%"
if not exist "%SERVER_DST%\genres" mkdir "%SERVER_DST%\genres"
copy /y "%SERVER_SRC%\*.py" "%SERVER_DST%\" >nul
copy /y "%SERVER_SRC%\requirements.txt" "%SERVER_DST%\" >nul
if exist "%SERVER_SRC%\genres\*" (
    xcopy /y /e /q "%SERVER_SRC%\genres\*" "%SERVER_DST%\genres\" >nul 2>&1
)
echo   Server files: %SERVER_DST%\

:: Find the .amxd device
set "AMXD_SRC="
if exist "%SCRIPT_DIR%dist\Conduit\Conduit.amxd" (
    set "AMXD_SRC=%SCRIPT_DIR%dist\Conduit\Conduit.amxd"
) else if exist "%SCRIPT_DIR%m4l\Conduit.amxd" (
    set "AMXD_SRC=%SCRIPT_DIR%m4l\Conduit.amxd"
) else (
    echo   Building device...
    if exist "%SCRIPT_DIR%m4l\build-device.py" (
        !PYTHON_CMD! "%SCRIPT_DIR%m4l\build-device.py"
        if exist "%SCRIPT_DIR%m4l\Conduit.amxd" (
            set "AMXD_SRC=%SCRIPT_DIR%m4l\Conduit.amxd"
        )
    )
)

set "BRIDGE_SRC=%SCRIPT_DIR%m4l\conduit-bridge.js"
if not exist "!BRIDGE_SRC!" (
    if exist "%SCRIPT_DIR%dist\Conduit\conduit-bridge.js" (
        set "BRIDGE_SRC=%SCRIPT_DIR%dist\Conduit\conduit-bridge.js"
    )
)

:: Install to Ableton User Library
set "INSTALL_DIR="
if exist "%ABLETON_MIDI_FX%" (
    set "INSTALL_DIR=%ABLETON_MIDI_FX%\Conduit"
) else if exist "%DOCS_DIR%\Ableton\User Library\Presets\MIDI Effects" (
    mkdir "%ABLETON_MIDI_FX%" 2>nul
    set "INSTALL_DIR=%ABLETON_MIDI_FX%\Conduit"
) else (
    echo   WARNING: Ableton User Library not found.
    echo   You'll need to copy the device manually.
)

if defined INSTALL_DIR (
    if not exist "!INSTALL_DIR!" mkdir "!INSTALL_DIR!"
    if defined AMXD_SRC (
        copy /y "!AMXD_SRC!" "!INSTALL_DIR!\" >nul
        echo   Device: !INSTALL_DIR!\Conduit.amxd
    ) else (
        echo   WARNING: Conduit.amxd not found — device not installed.
        echo   Build it with: python m4l\build-device.py
    )
    if exist "!BRIDGE_SRC!" (
        copy /y "!BRIDGE_SRC!" "!INSTALL_DIR!\" >nul
        echo   Bridge: !INSTALL_DIR!\conduit-bridge.js
    )
    :: Unblock downloaded files so Windows doesn't prevent M4L from loading them
    powershell -NoProfile -Command "Get-ChildItem '!INSTALL_DIR!' | Unblock-File" >nul 2>&1
)

:: Install conduit-bridge.js to Max Packages
set "INSTALLED_PKG=0"
for %%d in ("%MAX8_PKG%" "%MAX9_PKG%") do (
    for %%p in ("%%~dpd..") do (
        if exist "%%~fp" (
            if not exist "%%~d" mkdir "%%~d"
            if exist "!BRIDGE_SRC!" (
                copy /y "!BRIDGE_SRC!" "%%~d\" >nul
                powershell -NoProfile -Command "Unblock-File '%%~d\conduit-bridge.js'" >nul 2>&1
                echo   Max Package: %%~d\conduit-bridge.js
                set "INSTALLED_PKG=1"
            )
        )
    )
)

if "!INSTALLED_PKG!"=="0" (
    echo   NOTE: Max Packages directory not found ^(Max 8 or Max 9^).
    echo   conduit-bridge.js was installed next to Conduit.amxd — this usually works.
    echo   If the device won't load, open Max ^> File ^> Show File Browser, and add
    echo   the Conduit install folder to Max's search path.
)

:: ── Done ─────────────────────────────────────────────────────
echo.
echo ================================================
echo   Conduit installed successfully!
echo ================================================
echo.
echo   To use Conduit:
echo   1. Open Ableton Live
echo   2. Go to Browser ^> User Library ^> MIDI Effects ^> Conduit
echo   3. Drag Conduit onto a MIDI track
echo   4. The server starts automatically — no terminal needed
echo.
echo   Server files: %CONDUIT_HOME%\server\
echo   Server log:   %CONDUIT_HOME%\server.log
echo.
pause
