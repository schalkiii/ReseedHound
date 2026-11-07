@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] python not found. Install Python 3.11+ and add it to PATH.
    echo.
    pause
    exit /b 1
)

if not exist "config.yaml" (
    echo [ERROR] config.yaml not found. Copy config.example.yaml to config.yaml first.
    echo.
    pause
    exit /b 1
)

echo Starting SeedHound ...
echo Note: with scheduler.enabled=true this runs in persistent schedule mode.
echo       Set scheduler.enabled=false in config.yaml for a one-shot reseed.
echo.
python run.py %*
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" (
    echo [ERROR] SeedHound failed with exit code %EXIT_CODE%. Check output above and logs\.
) else (
    echo [OK] SeedHound finished.
)
echo.
pause
exit /b %EXIT_CODE%
