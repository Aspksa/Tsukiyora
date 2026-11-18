@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 (
  echo Cannot open Tsukiyora folder.
  pause
  exit /b 1
)
set "TSUKIYORA_PY="
set "TSUKIYORA_FLAGS="
if /I "%~1"=="--self-test" set "TSUKIYORA_FLAGS=--self-test"
if exist "%~dp0runtime\python\python.exe" set "TSUKIYORA_PY=%~dp0runtime\python\python.exe"
if defined TSUKIYORA_PY (
  "%TSUKIYORA_PY%" "%~dp0launcher.py" %TSUKIYORA_FLAGS%
  goto result
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%~dp0launcher.py" %TSUKIYORA_FLAGS%
  goto result
)
where python >nul 2>nul
if not errorlevel 1 (
  python "%~dp0launcher.py" %TSUKIYORA_FLAGS%
  goto result
)
echo Python 3.10 or newer not found.
echo Install Python or use runtime\python\python.exe
pause
exit /b 1
:result
if errorlevel 1 (
  echo Start failed. Check data\logs\launcher.log
  pause
  exit /b 1
)
exit /b 0
