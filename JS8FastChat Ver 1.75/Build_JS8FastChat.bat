@echo off
REM ============================================================================
REM  Build_JS8FastChat.bat  --  rebuild JS8FastChat.exe
REM  JS8MapChat 1.75        Lives in: JS8FastChat Ver 1.75\  (beside the .spec)
REM ============================================================================
REM  Just double-click it, or run it from a prompt. It cd's to its own folder,
REM  so it does not matter what directory you are in when you start it.
REM
REM  It DELETES build\ and dist\ first. That is on purpose: a stale exe left
REM  behind by a failed build is exactly how you end up debugging yesterday's
REM  binary. Both folders are 100%% regenerable -- nothing of yours is in them.
REM
REM  It prints the TIMESTAMP of the exe at the end. If that timestamp is not
REM  from a few seconds ago, YOU ARE ABOUT TO TEST THE WRONG BINARY. Look at it
REM  every single time.
REM ============================================================================

cd /d "%~dp0"

echo.
echo ================================================================
echo  Building JS8FastChat.exe
echo  Folder: %CD%
echo ================================================================
echo.

if not exist "JS8FastChat.spec" (
    echo [ERROR] JS8FastChat.spec not found in this folder.
    echo         This .bat must live in the app root, beside the spec.
    goto :end
)

REM -- #35 build gate: refuse to build if the two shared_resolver.py copies -----
REM    have diverged. check_twins.bat lives one level up, at the project root.
call "%~dp0..\check_twins.bat"
if errorlevel 1 (
    echo.
    echo [ABORT] #35 gate failed -- see the message above. NOT building.
    echo         The two shared_resolver.py copies must be identical first.
    goto :end
)

echo [1/4] Removing old build\ and dist\ ...
if exist "build" rmdir /s /q "build"
if exist "dist"  rmdir /s /q "dist"

echo [2/4] Running PyInstaller ...
echo.
pyinstaller JS8FastChat.spec
echo.

echo [3/4] Verifying the exe ...
echo.
if exist "dist\JS8FastChat.exe" (
    echo   BUILD OK.
    echo.
    REM  Pipe-free timestamp readout. The old  dir ^| findstr  line broke
    REM  inside this parenthesized block: cmd turned ^| into a LITERAL pipe,
    REM  dir got "/i" as an argument, and printed  Invalid switch - "i".
    for %%F in ("dist\JS8FastChat.exe") do echo   %%~tF   %%~zF bytes   %%~fF
    echo.
    echo   ^>^> CHECK THE TIMESTAMP ABOVE. It must be from SECONDS ago.
    echo   ^>^> If it is not, you are about to test an OLD binary.
    echo.
    echo   Next: start JS8Map and GENERATE A MAP first -- no map means no
    echo         HTTP server and no data, and every test is invalid.
) else (
    echo   [FAILED] dist\JS8FastChat.exe was NOT created.
    echo            Scroll up for the PyInstaller error.
    echo            Your script-mode FastChat is untouched and still works.
)


REM -- [4/4] Compile the Windows installer -------------------------------------
REM    Matches what Build_JS8Map.bat does. Without this the build stopped at
REM    dist\JS8FastChat.exe, which is a file only YOU can use -- there was no
REM    installer to hand another operator, which is why FastChat could not be
REM    shipped at all. Skipped automatically if the exe was not produced.
if not exist "dist\JS8FastChat.exe" goto :end

echo [4/4] Compiling Windows installer ...
echo.

if not exist "JS8FastChat.iss" (
    echo   [SKIP] JS8FastChat.iss not found -- no installer built.
    echo          The exe above is still good.
    goto :end
)

set ISCC=""
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set ISCC="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\Inno Setup 6\ISCC.exe"       set ISCC="C:\Program Files\Inno Setup 6\ISCC.exe"

if %ISCC%=="" (
    echo   [NOTICE] Inno Setup not found -- no installer built.
    echo            Download free from: https://jrsoftware.org/isdl.php
    echo            The exe above is still good.
    goto :end
)

mkdir installer_output 2>nul
%ISCC% JS8FastChat.iss
if errorlevel 1 (
    echo.
    echo   [FAILED] Inno Setup compilation failed. Scroll up for the error.
    echo            The exe above is still good.
    goto :end
)

echo.
if exist "installer_output\JS8FastChat_v1.75_Setup.exe" (
    echo   INSTALLER OK.
    echo.
    for %%F in ("installer_output\JS8FastChat_v1.75_Setup.exe") do echo   %%~tF   %%~zF bytes   %%~fF
    echo.
    echo   ^>^> CHECK THE TIMESTAMP ABOVE. It must be from SECONDS ago.
    echo.
    echo   Give this ONE file to anyone who wants JS8FastChat.
    echo   Install JS8Map FIRST -- FastChat reads the databases it downloads.
) else (
    echo   [FAILED] installer_output\JS8FastChat_v1.75_Setup.exe was NOT created.
)

:end
echo.
echo ================================================================
pause
