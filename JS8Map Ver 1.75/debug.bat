@echo off
REM ===================================================================
REM  debug.bat  --  Launch JS8Map FROM SOURCE (not the frozen .exe).
REM
REM  WHY THIS EXISTS: the dist\JS8Map.exe is a FROZEN snapshot. It does
REM  NOT read your live JS8Map.py, so testing against it proves nothing
REM  about code changes. Run THIS to test the actual source. After a
REM  source test passes, rebuild with Build_JS8Map.bat and test the exe
REM  too -- source success != frozen success.
REM
REM  Place this file in the "JS8Map Ver 1.75" folder (next to JS8Map.py).
REM  %~dp0 makes it run from its own folder regardless of where launched.
REM ===================================================================
cd /d "%~dp0"
"C:\Users\KW3KW\AppData\Local\Programs\Python\Python314\python.exe" JS8Map.py
echo.
echo === JS8Map exited. Close this window or press a key. ===
pause
