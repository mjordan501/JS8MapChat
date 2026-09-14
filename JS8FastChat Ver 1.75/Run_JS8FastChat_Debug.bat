@echo off
setlocal
cd /d "%~dp0"
echo JS8FastChat 1.75 debug launcher
echo.
if not exist "JS8FastChat.py" ( echo ERROR: entry .py missing & pause & exit /b 1 )
if not exist "js8fastchat\app.py" ( echo ERROR: js8fastchat\app.py missing & pause & exit /b 1 )
python "JS8FastChat.py"
echo.
echo JS8FastChat closed or Python exited.
pause
