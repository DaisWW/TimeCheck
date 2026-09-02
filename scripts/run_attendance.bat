@echo off
setlocal
cd /d "%~dp0"

set "APP=%~dp0dingding-attendance.exe"
if not exist "%APP%" (
    echo Application not found: %APP%
    if "%~1"=="" pause
    exit /b 1
)

"%APP%" %*
set "EXITCODE=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %EXITCODE%
