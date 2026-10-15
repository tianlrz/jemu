@echo off
set "DECKROOM_PYTHONW=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
if exist "%DECKROOM_PYTHONW%" goto launch
for %%I in (pythonw.exe) do set "DECKROOM_PYTHONW=%%~$PATH:I"
if exist "%DECKROOM_PYTHONW%" goto launch
echo Python 3.10 or newer is required. Please install Python and try again.
pause
exit /b 1
:launch
start "" "%DECKROOM_PYTHONW%" "%~dp0launch.pyw"
