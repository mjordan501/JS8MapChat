#!/bin/bash
# ============================================
#  JS8MapChat 1.75  --  Build JS8FastChat for Linux
#  73 de KW3KW
# ============================================
#
# Mirrors Build_JS8FastChat.bat. Differences, all on purpose:
#   - No Inno Setup step. Packaging is a separate script, as with JS8Map.
#   - The twin check IS included here. check_twins.bat is a Windows script and
#     was never ported; this is that port. See the section below.
#
# Run it from the folder it lives in.

cd "$(dirname "$0")" || exit 1

echo
echo " ============================================"
echo "  JS8MapChat 1.75  --  Build JS8FastChat (Linux)"
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

# -- THE TWIN CHECK -----------------------------------------------------------
# shared_resolver.py exists in BOTH apps and MUST be byte-identical. It decides
# where the JS8Map <-> FastChat mailbox lives. If the two copies disagree, the
# apps compute DIFFERENT folders, every cross-app button stops working, and
# nothing reports an error anywhere -- the failure is completely silent.
#
# On Windows this is check_twins.bat. Nothing did it on Linux until now.
#
# Checked BEFORE the build, because a mismatch caught here costs nothing and a
# mismatch shipped costs an operator their afternoon.
# Finding JS8Map's copy. The first version of this assumed the two source
# folders are siblings, which is true on a developer's machine and NOT true
# inside the build container -- there the source is mounted at /src and its
# parent is the container root. That produced "//JS8Map/shared_resolver.py"
# and stopped the build for the wrong reason.
#
# So: try the layouts that actually occur, in order, and say which one was
# used. JS8MAP_SRC always wins if it is set.
MINE="js8fastchat/shared_resolver.py"
THEIRS=""
JS8MAP_USED=""

_candidates=""
if [ -n "${JS8MAP_SRC:-}" ]; then
    _candidates="$JS8MAP_SRC"
fi
_parent="$(cd .. 2>/dev/null && pwd)"
_candidates="$_candidates
/js8map
${_parent}/JS8Map
${_parent}/JS8Map Ver 1.75
${HOME}/Applications/JS8Map"

while IFS= read -r _c; do
    [ -n "$_c" ] || continue
    if [ -f "${_c}/shared_resolver.py" ]; then
        THEIRS="${_c}/shared_resolver.py"
        JS8MAP_USED="$_c"
        break
    fi
done <<EOF
$_candidates
EOF

echo " Checking the shared_resolver.py twin..."
if [ ! -f "$MINE" ]; then
    echo " [ERROR] Missing file: $MINE"
    exit 1
fi
if [ -z "$THEIRS" ]; then
    echo " [ERROR] Cannot find JS8Map's copy of shared_resolver.py."
    echo
    echo " Looked in:"
    while IFS= read -r _c; do
        [ -n "$_c" ] && echo "     $_c"
    done <<EOF
$_candidates
EOF
    echo
    echo " This check is not optional. A mismatch between the two copies breaks"
    echo " every cross-app button SILENTLY, with no error on either side."
    echo
    echo " If JS8Map's source is somewhere else, say where and run again:"
    echo "     JS8MAP_SRC=/path/to/JS8Map ./build_js8fastchat_linux.sh"
    echo
    echo " Building in the container? Mount JS8Map's folder read-only at"
    echo " /js8map and it will be found without setting anything:"
    echo "     -v ~/Applications/JS8Map:/js8map:ro,Z"
    exit 1
fi
echo " Comparing against: ${JS8MAP_USED}"

MINE_SUM="$(sha256sum "$MINE"   | cut -d' ' -f1)"
THEIRS_SUM="$(sha256sum "$THEIRS" | cut -d' ' -f1)"

if [ "$MINE_SUM" != "$THEIRS_SUM" ]; then
    echo " [ERROR] The two copies of shared_resolver.py DO NOT MATCH."
    echo
    echo "   FastChat : $MINE_SUM"
    echo "              $MINE"
    echo "   JS8Map   : $THEIRS_SUM"
    echo "              $THEIRS"
    echo
    echo " Nothing has been built. Make the two identical before building."
    echo " See what differs with:"
    echo "     diff \"$MINE\" \"$THEIRS\""
    exit 1
fi
echo " Twin check passed. Both copies are identical."
echo "   sha256: $MINE_SUM"
echo

# -- Check all required files are present -------------------------------------
echo " Checking required files..."
MISSING=0
for F in JS8FastChat.py JS8FastChat_linux.spec \
         js8fastchat/__init__.py js8fastchat/app.py js8fastchat/constants.py \
         js8fastchat/shared_resolver.py \
         js8fastchat/ui/main_window.py ; do
    if [ ! -f "$F" ]; then
        echo " [ERROR] Missing file: $F"
        MISSING=1
    fi
done
if [ "$MISSING" -ne 0 ]; then
    echo " Run this script from inside the JS8FastChat folder."
    exit 1
fi
echo " All files found."

# The icon is optional. Its absence costs a text button, not a crash, so it is
# reported rather than fatal -- same rule as the Windows spec.
if [ ! -f "js8map_icon.png" ]; then
    echo " [NOTE] js8map_icon.png is missing. The JS8Map button will show text"
    echo "        instead of an icon. Not fatal."
fi
echo

# -- Build --------------------------------------------------------------------
echo " Building JS8FastChat (this takes 1-2 minutes)..."
pyinstaller JS8FastChat_linux.spec --clean --noconfirm
if [ $? -ne 0 ]; then
    echo " [ERROR] PyInstaller build failed. See output above."
    exit 1
fi
echo " Program built successfully."
echo

# -- Prove it is actually there -----------------------------------------------
if [ ! -f "dist/JS8FastChat" ]; then
    echo " [ERROR] The build reported success but dist/JS8FastChat is not there."
    exit 1
fi
echo " Contents of dist/:"
ls -la dist/
echo

echo " ============================================"
echo "  BUILD COMPLETE"
echo
echo "  Run it with:"
echo "    ./dist/JS8FastChat"
echo
echo "  NOTE: FastChat needs JS8Map running to be useful. On its own the"
echo "  cross-app buttons have nothing to talk to."
echo " ============================================"
echo
