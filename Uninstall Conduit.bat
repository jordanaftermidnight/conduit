@echo off
setlocal enabledelayedexpansion

:: Uninstall Conduit.bat — Remove all installed Conduit files (Windows)
::
:: This removes the server, device, and bridge script installed by
:: "Install Conduit.bat". It does NOT remove Ollama itself,
:: Python, or the downloaded Conduit source folder.

title Conduit — Uninstaller

cls
echo ================================================
echo    Conduit — Uninstaller
echo ================================================
echo.

set "REMOVED=0"

:: ── Remove server files ──────────────────────────────────────
set "CONDUIT_HOME=%USERPROFILE%\Documents\Conduit"
if exist "%CONDUIT_HOME%" (
    rmdir /s /q "%CONDUIT_HOME%"
    echo   Removed: %CONDUIT_HOME%\
    set "REMOVED=1"
) else (
    echo   Not found: %CONDUIT_HOME%\ ^(already removed^)
)

:: ── Remove device from Ableton User Library ──────────────────
set "ABLETON_DIR=%USERPROFILE%\Documents\Ableton\User Library\Presets\MIDI Effects\Max MIDI Effect\Conduit"
if exist "%ABLETON_DIR%" (
    rmdir /s /q "%ABLETON_DIR%"
    echo   Removed: %ABLETON_DIR%\
    set "REMOVED=1"
) else (
    echo   Not found: Ableton User Library device ^(already removed^)
)

:: ── Remove bridge from Max Packages ──────────────────────────
for %%d in (
    "%USERPROFILE%\Documents\Max 8\Packages\Conduit"
    "%USERPROFILE%\Documents\Max 9\Packages\Conduit"
) do (
    if exist "%%~d" (
        rmdir /s /q "%%~d"
        echo   Removed: %%~d\
        set "REMOVED=1"
    )
)

:: ── Ask about Ollama model ───────────────────────────────────
echo.
where ollama >nul 2>&1
if %errorlevel%==0 (
    ollama list 2>nul | findstr /c:"llama3.2" >nul 2>&1
    if !errorlevel!==0 (
        set /p "REMOVE_MODEL=  Remove llama3.2 model? (~2GB) (y/n): "
        if /i "!REMOVE_MODEL!"=="y" (
            ollama rm llama3.2
            echo   Removed: llama3.2 model
            set "REMOVED=1"
        ) else (
            echo   Kept: llama3.2 model
        )
    )
)

:: ── Summary ──────────────────────────────────────────────────
echo.
echo ================================================
if !REMOVED!==1 (
    echo   Conduit uninstalled.
) else (
    echo   Nothing to remove — Conduit was not installed.
)
echo ================================================
echo.
echo   NOT removed:
echo   - Ollama ^(system app^)
echo   - Python and pip packages
echo   - This Conduit source folder
echo.
pause
