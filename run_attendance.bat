@echo off
setlocal
cd /d "%~dp0"

python -m attendance_analyzer.cli %*
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
    pause
) else if "%~1"=="" (
    pause
)
exit /b %EXITCODE%
