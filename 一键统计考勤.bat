@echo off
setlocal
if not "%~1"=="" (
    echo No arguments are needed. Use input, output and config.json beside this BAT.
    exit /b 2
)
cd /d "%~dp0"

python -X utf8 -u -m attendance_analyzer.cli "%~dp0input" -o "%~dp0output" -c "%~dp0config.json"
exit /b %ERRORLEVEL%
