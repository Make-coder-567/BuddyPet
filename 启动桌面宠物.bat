@echo off
rem WorkBuddy Desktop Pet launcher
setlocal
cd /d "%~dp0"

set "PYW=%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe"
if exist "%PYW%" goto run
set "PYW=%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
if exist "%PYW%" goto run
set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if exist "%PYW%" goto run

for /f "delims=" %%I in ('where pythonw.exe 2^>nul') do (
    set "PYW=%%I"
    goto run
)

echo pythonw.exe not found. Install Python 3, then run: pip install -r requirements.txt
pause
exit /b 1

:run
start "" "%PYW%" "%~dp0main.py"
exit /b 0
