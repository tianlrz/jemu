@echo off
setlocal EnableExtensions
rem Same lookup as Start.cmd: bundled runtime, PATH without the Microsoft Store placeholder, py launcher.
set "JEMU_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%JEMU_PYTHON%" goto check
set "JEMU_PYTHON="
for /f "delims=" %%I in ('where python.exe 2^>nul ^| findstr /v /i /c:"WindowsApps"') do if not defined JEMU_PYTHON set "JEMU_PYTHON=%%I"
if defined JEMU_PYTHON goto check
for /f "delims=" %%I in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do if not defined JEMU_PYTHON set "JEMU_PYTHON=%%I"
if not defined JEMU_PYTHON goto missing
:check
"%JEMU_PYTHON%" -c "import sys;sys.exit(sys.version_info<(3,10))" >nul 2>&1
if errorlevel 1 goto missing
"%JEMU_PYTHON%" "%~dp0jemu.py" %*
exit /b %errorlevel%
:missing
echo Python 3.10 or newer is required. 1>&2
exit /b 3
