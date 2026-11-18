@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0" || (echo Cannot open Tsukiyora folder & pause & exit /b 1)
where py >nul 2>nul
if not errorlevel 1 (
  for %%V in (3.13 3.12 3.11 3.10) do (
    py -%%V --version >nul 2>nul
    if not errorlevel 1 (
      py -%%V "%~dp0launcher.py"
      goto :finish
    )
  )
  py -3 --version >nul 2>nul
  if not errorlevel 1 (
    py -3 "%~dp0launcher.py"
    goto :finish
  )
)
where python >nul 2>nul
if not errorlevel 1 (
  python "%~dp0launcher.py"
  goto :finish
)
echo Python 3.10 or newer is required. Install Python and retry.
pause
exit /b 1
:finish
if errorlevel 1 pause
endlocal
