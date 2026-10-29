@echo off
setlocal EnableExtensions
rem Find Python 3.10+: the bundled Codex runtime, then PATH (skipping the Microsoft Store
rem placeholder, which opens the Store instead of running Python), then the py launcher.
set "JEMU_PYW=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
if exist "%JEMU_PYW%" goto check
set "JEMU_PYW="
for /f "delims=" %%I in ('where pythonw.exe 2^>nul ^| findstr /v /i /c:"WindowsApps"') do if not defined JEMU_PYW set "JEMU_PYW=%%I"
if defined JEMU_PYW goto check
set "JEMU_PY="
for /f "delims=" %%I in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do if not defined JEMU_PY set "JEMU_PY=%%I"
if not defined JEMU_PY goto missing
set "JEMU_PYW=%JEMU_PY:python.exe=pythonw.exe%"
:check
if not exist "%JEMU_PYW%" goto missing
set "JEMU_PY=%JEMU_PYW:pythonw.exe=python.exe%"
if not exist "%JEMU_PY%" set "JEMU_PY=%JEMU_PYW%"
"%JEMU_PY%" -c "import sys;sys.exit(sys.version_info<(3,10))" >nul 2>&1
if errorlevel 1 goto old
rem JEMU_DRY_RUN only reports the interpreter; CI uses it to check this script.
if defined JEMU_DRY_RUN goto report
start "" "%JEMU_PYW%" "%~dp0launch.pyw"
exit /b 0
:report
echo %JEMU_PYW%
exit /b 0
:old
echo Jemu needs Python 3.10 or newer. This Python is too old:
echo %JEMU_PY%
goto help
:missing
echo Jemu needs Python 3.10 or newer, but no Python installation was found.
:help
echo Install Python from https://www.python.org/downloads/ and run Start.cmd again.
if not defined JEMU_DRY_RUN pause
exit /b 1
