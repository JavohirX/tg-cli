@echo off
setlocal
chcp 65001 >nul
title tg-cli

rem Build in the tg-cli folder itself, wherever this file is started from.
cd /d "%~dp0"

rem The install is editable, so code changes need no rebuild. Rebuild when
rem pyproject.toml changed (dependencies, entry point) or when Python's
rem tg_cli does not point at this folder.
set "STAMP=.build-stamp"
set "HASH="
for /f "delims=" %%H in ('certutil -hashfile pyproject.toml SHA256 ^| findstr /v ":"') do if not defined HASH set "HASH=%%H"
set "BUILT="
if exist "%STAMP%" set /p BUILT=<"%STAMP%"
set "NEED_BUILD="
if not "%HASH%"=="%BUILT%" set "NEED_BUILD=1"
py -3.12 -I -c "import os, sys, tg_cli; sys.exit(not os.path.samefile(os.path.dirname(os.path.dirname(tg_cli.__file__)), sys.argv[1]))" "%CD%" 2>nul || set "NEED_BUILD=1"

if not defined NEED_BUILD goto run
echo Building tg-cli in "%CD%"...
py -3.12 -m pip install -e . --no-build-isolation --disable-pip-version-check -q
if errorlevel 1 goto build_failed
>"%STAMP%" echo %HASH%

:run
py -3.12 -m tg_cli %*
exit /b %errorlevel%

:build_failed
echo Build failed. See the pip output above.
pause
exit /b 1
