@echo off
setlocal
cd /d "%~dp0"

python -m pip install -r requirements.txt
if errorlevel 1 goto finished
python -X utf8 -c "from pathlib import Path; from attendance_analyzer.config import AppConfig; AppConfig.load(Path('config.json'))"
if errorlevel 1 goto finished
python -X utf8 -m unittest discover -s tests
if errorlevel 1 goto finished
python -X utf8 scripts\package_release.py

:finished
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
    echo.
    echo Build failed. Read the error above. No new release was completed.
)
echo.
pause
exit /b %EXITCODE%
