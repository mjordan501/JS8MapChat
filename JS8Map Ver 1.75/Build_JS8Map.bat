@echo off
title JS8MapChat 1.75 - Build Installer
color 1F
cls

:: -- Run from THIS script's own folder, whatever the caller's cwd was ---------
::    Without this, launching by full path (e.g. from C:\>) runs the required-
::    files check and PyInstaller against the CALLER's current directory, so the
::    check below fails with "Missing file: JS8Map.py" even though every file is
::    present next to this .bat. That bug bit on 2026-07-15; this is the fix.
cd /d "%~dp0"

echo.
echo  ============================================
echo   JS8MapChat 1.75  --  Build Installer
echo   73 de KW3KW
echo  ============================================
echo.

:: -- Check Python ------------------------------------------------------------
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python is not installed or not in PATH.
    echo  Download from: https://python.org
    pause & exit /b 1
)

:: -- Check all required files are present -------------------------------------
echo  Checking required files...
:: The five offline-map files were added 2026-08-26. Without them the map page
:: loads no library and draws no borders, so it opens BLANK rather than merely
:: plain -- and it does so silently. Checked here so a missing one stops the
:: build instead of shipping.
for %%F in (JS8Map.py zip3_latlon.py index.html app.js app.css JS8Map.spec JS8Map_icon.ico shared_resolver.py leaflet.js leaflet.css js8map_borders.json js8map_admin1.json js8map_places.json js8map_lakes.json) do (
    if not exist "%%F" (
        echo  [ERROR] Missing file: %%F
        echo  All files must be in the same folder as this batch file.
        pause & exit /b 1
    )
)
echo  All files found.
echo.

:: -- #35 build gate: the two shared_resolver.py copies MUST be identical ------
::    check_twins.bat lives one level up, at the project root.
call "%~dp0..\check_twins.bat"
if errorlevel 1 (
    echo  [ABORT] #35 gate failed -- see the message above. NOT building.
    echo         The two shared_resolver.py copies must be identical first.
    pause & exit /b 1
)
echo.

:: -- Install dependencies ----------------------------------------------------
echo  Step 1 of 3:  Installing PyInstaller...
pip install pyinstaller --quiet --upgrade
if errorlevel 1 (
    echo  [ERROR] pip install failed. Check your internet connection.
    pause & exit /b 1
)
echo  Dependencies installed.
echo.

:: -- Build the .exe with PyInstaller -----------------------------------------
echo  Step 2 of 3:  Building JS8Map.exe (this takes 1-2 minutes)...
pyinstaller JS8Map.spec --clean --noconfirm
if errorlevel 1 (
    echo  [ERROR] PyInstaller build failed. See output above.
    pause & exit /b 1
)
echo  Executable built successfully.
echo.

:: -- Put the offline map files beside the fresh .exe -------------------------
::    PyInstaller runs with --clean, so it deletes and rebuilds dist\ every
::    time. These five files are NOT packed inside the exe (deliberately -- a
::    one-file exe unpacks its whole payload to temp on every launch, and this
::    is ~1.8 MB of data that never changes). The installer puts them next to
::    JS8Map.exe on the operator's machine; this does the same in dist\ so you
::    can run dist\JS8Map.exe directly to test without installing.
::
::    Without this you get a map with no borders and no place names after
::    every build, which looks like a code fault and is not one.
echo  Copying offline map files into dist...
copy /y "leaflet.js"          dist\ >nul
copy /y "leaflet.css"         dist\ >nul
copy /y "js8map_borders.json" dist\ >nul
copy /y "js8map_admin1.json"  dist\ >nul
copy /y "js8map_places.json"  dist\ >nul
copy /y "js8map_lakes.json"   dist\ >nul
if errorlevel 1 (
    echo  [ERROR] Could not copy the map files into dist.
    pause & exit /b 1
)
echo  Map files copied.
echo.

:: -- Compile the Inno Setup installer ----------------------------------------
echo  Step 3 of 3:  Compiling Windows installer...

:: Try standard Inno Setup locations
set ISCC=""
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set ISCC="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\Inno Setup 6\ISCC.exe"       set ISCC="C:\Program Files\Inno Setup 6\ISCC.exe"

if %ISCC%=="" (
    echo  [NOTICE] Inno Setup not found.
    echo  Download free from: https://jrsoftware.org/isdl.php
    echo  Then run:  ISCC.exe JS8Map.iss
    echo.
    echo  Your .exe is still ready at:
    echo    dist\JS8Map.exe
    echo.
    pause & exit /b 0
)

mkdir installer_output 2>nul
%ISCC% JS8Map.iss
if errorlevel 1 (
    echo  [ERROR] Inno Setup compilation failed.
    pause & exit /b 1
)

echo.
echo  ============================================
echo   BUILD COMPLETE!
echo.
echo   Installer ready at:
echo   installer_output\JS8Map_v1.75_Setup.exe
echo.
echo   Give this ONE file to anyone.
echo   They just double-click and install.
echo   No Python needed on their machine.
echo  ============================================
echo.
pause
