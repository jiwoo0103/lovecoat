@echo off
cd /d "%~dp0"
if not exist "dist-releases\latest.txt" goto source
set /p coatdeskExecutable=<"dist-releases\latest.txt"
if not exist "%~dp0%coatdeskExecutable%" goto source
start "" "%~dp0%coatdeskExecutable%"
exit /b
:source
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "run_app.py"
    exit /b
)
echo Please build the app or set up the Python environment described in README.md.
pause
