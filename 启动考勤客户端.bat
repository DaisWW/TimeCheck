@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

wscript.exe "%~dp0启动考勤工作台.vbs"
exit /b %ERRORLEVEL%
