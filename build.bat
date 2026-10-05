@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo   PageFlow - build Windows exe
echo   Created by Amman
echo ==========================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found.
  echo Install 64-bit Python from https://www.python.org and tick "Add python.exe to PATH",
  echo then run this file again.
  pause
  exit /b 1
)

rem The build runs in its own environment in a SHORT folder on the system drive.
rem This avoids the Windows 260-character path limit, and it must not be under
rem AppData, because the Microsoft Store Python silently redirects that folder.
set "VENV=%SystemDrive%\PageFlowBuild"
set "PY=%VENV%\Scripts\python.exe"

if not exist "%PY%" (
  echo [1/3] Creating build environment in %VENV% ...
  python -m venv "%VENV%"
)

if not exist "%PY%" (
  echo Could not create it there. Trying the project folder instead...
  python -m venv "%~dp0.buildenv"
  set "VENV=%~dp0.buildenv"
  set "PY=%~dp0.buildenv\Scripts\python.exe"
)

if not exist "%PY%" (
  echo.
  echo Could not create a build environment.
  goto fail
)

echo Using build environment: %VENV%

echo.
echo [2/3] Installing requirements...
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto fail

echo.
echo [3/3] Building PageFlow.exe (this takes a few minutes)...
"%PY%" -m PyInstaller --noconfirm --clean --noconsole --onefile --name PageFlow --icon icon.ico --version-file version_info.txt --add-data "icon.png;." pageflow.py
if errorlevel 1 goto fail

echo.
echo ==========================================
echo   Done!  Your program is here:
echo   %~dp0dist\PageFlow.exe
echo ==========================================
pause
exit /b 0

:fail
echo.
echo The build failed. Read the messages above for the reason.
echo Tip: Python from python.org (64-bit, version 3.11 - 3.13) is the safest choice.
echo If it keeps failing, delete the folder %VENV% and run this file again.
pause
exit /b 1
