@echo off
setlocal
set "JEMU_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%JEMU_PYTHON%" goto run
for %%I in (python.exe) do set "JEMU_PYTHON=%%~$PATH:I"
if exist "%JEMU_PYTHON%" goto run
echo Python 3.10 or newer is required. 1>&2
exit /b 3
:run
"%JEMU_PYTHON%" "%~dp0jemu.py" %*
exit /b %errorlevel%
