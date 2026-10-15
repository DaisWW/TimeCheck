@echo off
setlocal
cd /d "%~dp0"

python main.py %*
set "EXITCODE=%ERRORLEVEL%"

if not "%EXITCODE%"=="0" (
    echo.
    echo The attendance client could not start. See the error above.
    echo Source mode requires Python with Tcl/Tk on PATH.
    echo Install dependencies: python -m pip install -r requirements.txt
    echo Without Python, extract the release ZIP and open dingding-attendance.exe.
    echo.
    pause
)
exit /b %EXITCODE%
