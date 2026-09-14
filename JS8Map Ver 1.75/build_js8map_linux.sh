#!/bin/bash
# ============================================
#  JS8MapChat 1.75  --  Build JS8Map for Linux
#  73 de KW3KW
# ============================================
#
# Mirrors Build_JS8Map.bat. Two differences, both on purpose:
#   - No Inno Setup step. There is no Windows installer on Linux. Phase 1 ends
#     with a runnable program in dist/, not with a package.
#   - No twin check. check_twins.bat is a Windows script.
#
# Run it from the folder it lives in.

cd "$(dirname "$0")" || exit 1

echo
echo " ============================================"
echo "  JS8MapChat 1.75  --  Build JS8Map (Linux)"
echo "  73 de KW3KW"
echo " ============================================"
echo

# -- Check Python -------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    echo " [ERROR] python3 is not installed or not on PATH."
    exit 1
fi
echo " Python: $(python3 --version)"

# -- Check PyInstaller --------------------------------------------------------
if ! command -v pyinstaller >/dev/null 2>&1; then
    echo " [ERROR] pyinstaller is not installed or not on PATH."
    echo " Install it with:  pip3 install --user pyinstaller"
    exit 1
fi
echo " PyInstaller: $(pyinstaller --version)"

# -- Check tkinter is present and packable ------------------------------------
# On Linux tkinter is a SEPARATE system package. If it is missing here it cannot
# be packed into the program, and the app will not start on any machine.
if ! python3 -c "import tkinter" >/dev/null 2>&1; then
    echo " [ERROR] tkinter is missing on this build machine."
    echo " Install it with:  sudo apt install python3-tk"
    exit 1
fi
echo " tkinter: present"
echo

# -- Check all required files are present -------------------------------------
# The six offline-map files were added 2026-08-26. Without them the map page
# loads no library and draws no borders, so it opens BLANK rather than merely
# plain -- and it does so silently. Checked here so a missing one stops the
# build instead of shipping.
echo " Checking required files..."
MISSING=0
for F in JS8Map.py zip3_latlon.py index.html app.js app.css \
         JS8Map_linux.spec JS8Map_icon.png shared_resolver.py \
         leaflet.js leaflet.css \
         js8map_borders.json js8map_admin1.json \
         js8map_places.json js8map_lakes.json ; do
    if [ ! -f "$F" ]; then
        echo " [ERROR] Missing file: $F"
        MISSING=1
    fi
done
if [ "$MISSING" -ne 0 ]; then
    echo " All files must be in the same folder as this script."
    exit 1
fi
echo " All files found."
echo

# -- Build --------------------------------------------------------------------
echo " Step 1 of 2:  Building JS8Map (this takes 1-2 minutes)..."
pyinstaller JS8Map_linux.spec --clean --noconfirm
if [ $? -ne 0 ]; then
    echo " [ERROR] PyInstaller build failed. See output above."
    exit 1
fi
echo " Program built successfully."
echo

# -- Put the offline map files beside the fresh program -----------------------
# PyInstaller runs with --clean, so it deletes and rebuilds dist/ every time.
# These six files are NOT packed inside the program (deliberately -- a one-file
# build unpacks its whole payload to temp on every launch, and this is ~2 MB of
# data that never changes). They must sit NEXT TO the program instead.
#
# Without this you get a map with no borders and no place names after every
# build, which looks like a code fault and is not one.
echo " Step 2 of 2:  Copying offline map files into dist..."
for F in leaflet.js leaflet.css js8map_borders.json js8map_admin1.json \
         js8map_places.json js8map_lakes.json ; do
    cp -f "$F" dist/ || { echo " [ERROR] Could not copy $F into dist."; exit 1; }
done
echo " Map files copied."
echo

# -- Prove they are actually there --------------------------------------------
echo " Contents of dist/:"
ls -la dist/
echo

echo " ============================================"
echo "  BUILD COMPLETE"
echo
echo "  Run it with:"
echo "    ./dist/JS8Map"
echo " ============================================"
echo
