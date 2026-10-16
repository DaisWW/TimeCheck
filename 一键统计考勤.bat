@echo off
setlocal
if not "%~1"=="" (
    echo No arguments are needed. Use input, output and config.json beside this BAT.
    exit /b 2
)
cd /d "%~dp0"

set "ATTENDANCE_PYTHON=%~dp0runtime\python.exe"
if not exist "%ATTENDANCE_PYTHON%" (
    if exist "%~dp0scripts\package_release.py" (
        set "ATTENDANCE_PYTHON=python"
    ) else (
        echo Runtime is incomplete. Fully extract the release ZIP and try again.
        exit /b 2
    )
)
"%ATTENDANCE_PYTHON%" -s -B -X utf8 -u -m attendance_analyzer.cli "%~dp0input" -o "%~dp0output" -c "%~dp0config.json"
exit /b %ERRORLEVEL%
