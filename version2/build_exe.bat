@echo off
setlocal
title EBSPLIT Builder
cd /d "%~dp0"

REM ==========================================================
REM  Double-click this file to build dist\EBSPLIT.exe
REM  Requires 64-bit Windows + Python 3.10-3.13 (one time).
REM  Works offline using the bundled .whl files.
REM  (Korean instructions are in README.md)
REM ==========================================================

set "PY=python"
where python >nul 2>nul || set "PY=py"
where %PY% >nul 2>nul
if errorlevel 1 (
  echo.
  echo [ERROR] Python not found.
  echo   Install Python 3.10-3.13 from https://www.python.org/downloads/
  echo   and check "Add python.exe to PATH" during setup.
  echo.
  pause
  exit /b 1
)

REM  Wheels may sit in this folder or in a "wheels" sub-folder.
REM  %~dp0 always ends with a backslash. Passed as "...\" the trailing backslash
REM  escapes the closing quote, so pip receives one mangled argument and reports
REM  "You must give at least one requirement to install". Strip it.
set "WHEELDIR=%~dp0"
if "%WHEELDIR:~-1%"=="\" set "WHEELDIR=%WHEELDIR:~0,-1%"
if exist "%~dp0wheels\*.whl" set "WHEELDIR=%~dp0wheels"

REM ----------------------------------------------------------
REM  [1/5] Isolated build environment.
REM  PyInstaller bundles whatever it can reach from the Python it
REM  runs under. Building straight from Anaconda (or any Python with
REM  a large site-packages) drags numpy/scipy/pandas into the EXE and
REM  produces a ~750 MB file. A throw-away venv keeps it near 60 MB.
REM ----------------------------------------------------------
echo.
echo [1/5] Creating an isolated build environment ...
set "VENV=%~dp0.buildenv"
set "VPY=%VENV%\Scripts\python.exe"
if exist "%VPY%" (
  echo       reusing %VENV%
) else (
  %PY% -m venv "%VENV%"
)
if not exist "%VPY%" (
  echo   [WARN] venv could not be created; falling back to the system Python.
  echo          The EXE may be much larger than necessary.
  set "VPY=%PY%"
)

echo.
echo [2/5] Installing packages (offline first) ...
echo       wheel source: %WHEELDIR%
"%VPY%" -m pip install --no-index --find-links "%WHEELDIR%" pymupdf pyinstaller
if errorlevel 1 (
  echo   Offline install failed. Trying online ...
  "%VPY%" -m pip install --upgrade pip
  "%VPY%" -m pip install pymupdf pyinstaller
)
if errorlevel 1 goto err

echo.
echo [3/5] Generating application icon ...
"%VPY%" "%~dp0make_icon.py"
set "ICONARGS="
if exist "%~dp0ebsplit.ico" (
  set "ICONARGS=--icon "%~dp0ebsplit.ico" --add-data "%~dp0ebsplit.ico;.""
) else (
  echo   [WARN] Icon generation failed; building without a custom icon.
)

REM  Windows locks a running executable, so PyInstaller cannot replace
REM  dist\EBSPLIT.exe while the app is open. Without this check the build
REM  ends in a Python traceback (PermissionError / WinError 5) that says nothing
REM  about the real cause.
tasklist /FI "IMAGENAME eq EBSPLIT.exe" 2>nul | find /I "EBSPLIT.exe" >nul
if not errorlevel 1 (
  echo.
  echo [ERROR] EBSPLIT.exe is still running.
  echo         Close the application window, then run this script again.
  echo         Anything unsaved in it - region, move, settings - will be lost,
  echo         so finish or save your work first.
  echo.
  pause
  exit /b 1
)

echo.
echo [4/5] Building single EXE ... (may take a few minutes)
REM  --noconsole hides the black console window when the EXE is double-clicked.
REM  Command-line use still prints: the app re-attaches to the calling console.
REM  Remove --noconsole only if you need a console for debugging.
REM  The --exclude-module list is a safety net for the fallback case above.
"%VPY%" -m PyInstaller --onefile --noconsole --name EBSPLIT --clean ^
  --hidden-import ebsplit_gui --hidden-import ebsplit_config ^
  --exclude-module numpy --exclude-module scipy --exclude-module pandas ^
  --exclude-module matplotlib --exclude-module PIL --exclude-module IPython ^
  --exclude-module jupyter --exclude-module notebook --exclude-module sympy ^
  --exclude-module sqlalchemy --exclude-module tornado --exclude-module zmq ^
  --exclude-module pytest --exclude-module numba --exclude-module cv2 ^
  %ICONARGS% ^
  pdf_split_scale.py
if errorlevel 1 goto err

echo.
echo [5/5] DONE
for %%F in ("%~dp0dist\EBSPLIT.exe") do set "EXESIZE=%%~zF"
echo ==========================================================
echo   Portable app: %cd%\dist\EBSPLIT.exe
echo   Size: %EXESIZE% bytes  (expect roughly 30-40 MB)
echo   Copy that single EXE anywhere - no Python needed.
echo    - Double-click  : preview GUI, no console window
echo    - Command line  : EBSPLIT.exe input.pdf --scale 1.75 --paper A4
echo.
echo   Settings are stored in ebsplit_config.json next to the EXE
echo   (or in %%APPDATA%%\EBSPLIT if that folder is read-only).
echo.
echo   The .buildenv folder is only needed for building and can be deleted.
echo ==========================================================
echo.
pause
exit /b 0

:err
echo.
echo [ERROR] Build failed. Check Python installation and try again.
echo.
pause
exit /b 1
