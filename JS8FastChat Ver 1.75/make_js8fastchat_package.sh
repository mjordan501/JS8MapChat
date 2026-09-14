#!/bin/bash
# ============================================================================
#  make_js8fastchat_package.sh
#
#  Makes the shippable Linux package for JS8FastChat.
#
#  Run this on the BUILD machine, from the project folder, AFTER a successful
#  ./build_js8fastchat_linux.sh
#
#  Output:  JS8FastChat_v1.75_linux.tar.gz   in the project folder
#
#  That single .tar.gz is what goes to another Linux operator. They unpack it
#  and run install.sh. No root, no sudo, no package manager.
#
#  DELIBERATELY A SEPARATE PACKAGE FROM JS8MAP. The two apps release on their
#  own schedules; a FastChat fix must never require republishing JS8Map. The
#  cost of that choice is that mismatched versions CAN meet on an operator's
#  machine, so this script records the twin file's hash inside the package --
#  see section 3.
# ============================================================================

set -e

VERSION="1.75"
PKGNAME="JS8FastChat_v${VERSION}_linux"

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"
STAGE="${PROJECT_DIR}/pkgbuild/${PKGNAME}"

echo "============================================================"
echo " JS8FastChat Linux package builder"
echo " Project folder: ${PROJECT_DIR}"
echo "============================================================"
echo ""

# ---------------------------------------------------------------------------
# 1. The built program must exist
# ---------------------------------------------------------------------------
if [ ! -f "${PROJECT_DIR}/dist/JS8FastChat" ]; then
    echo "STOP: dist/JS8FastChat was not found."
    echo "      Build it first:  ./build_js8fastchat_linux.sh"
    exit 1
fi
echo "Found the built program: dist/JS8FastChat"

# ---------------------------------------------------------------------------
# 2. The icon
#
#    Linux launchers need a PNG. The .ico shipped on Windows is no use here --
#    Tk cannot read it and neither can the desktop menu. Absence costs a
#    generic icon, not a failure, so this is a note rather than a stop.
# ---------------------------------------------------------------------------
ICON_SRC=""
for guess in "${PROJECT_DIR}/js8fastchat_icon.png" \
             "${PROJECT_DIR}/JS8FastChat_icon.png" \
             "${PROJECT_DIR}/fastchat_icon.png"; do
    if [ -f "$guess" ]; then
        ICON_SRC="$guess"
        break
    fi
done

if [ -n "$ICON_SRC" ]; then
    echo "Found the icon: ${ICON_SRC}"
else
    echo "NOTE: no PNG icon found. The package will still build and install,"
    echo "      but the launcher will show a generic icon."
fi

# ---------------------------------------------------------------------------
# 3. Record the twin file's hash INSIDE the package.
#
#    shared_resolver.py exists in both apps and must be byte-identical. It
#    decides where the JS8Map <-> FastChat mailbox lives; if the two copies
#    disagree the apps compute DIFFERENT folders, every cross-app button stops
#    working, and NOTHING reports an error on either side.
#
#    build_js8fastchat_linux.sh already refuses to build on a mismatch, so the
#    copy inside this program is known good at build time. But the two apps
#    ship separately and update separately, so a mismatch can still arise LATER
#    on an operator's machine.
#
#    Recording the hash here does not prevent that. It makes it DIAGNOSABLE:
#    with this file present, "are these two installs a matched pair?" is one
#    comparison instead of an afternoon.
# ---------------------------------------------------------------------------
TWIN="${PROJECT_DIR}/js8fastchat/shared_resolver.py"
if [ ! -f "$TWIN" ]; then
    echo ""
    echo "STOP: js8fastchat/shared_resolver.py was not found."
    echo "      Nothing has been packaged."
    exit 1
fi
TWIN_SUM="$(sha256sum "$TWIN" | cut -d' ' -f1)"
echo "Twin file recorded: ${TWIN_SUM}"

# ---------------------------------------------------------------------------
# 4. Stage everything
# ---------------------------------------------------------------------------
rm -rf "${PROJECT_DIR}/pkgbuild"
mkdir -p "${STAGE}"

cp "${PROJECT_DIR}/dist/JS8FastChat" "${STAGE}/JS8FastChat"
chmod 755 "${STAGE}/JS8FastChat"

if [ -n "$ICON_SRC" ]; then
    cp "$ICON_SRC" "${STAGE}/js8fastchat.png"
fi

cat > "${STAGE}/TWIN_shared_resolver.sha256" <<TWIN_EOF
${TWIN_SUM}  shared_resolver.py
# JS8FastChat v${VERSION}, packaged $(date -u +%Y-%m-%dT%H:%M:%SZ)
#
# This is the SHA256 of the copy of shared_resolver.py built into this
# program. JS8Map carries a copy of the same file and the two MUST match.
#
# If the cross-app buttons stop working with no error on either side, compare
# this value against JS8Map's. A difference is the cause.
TWIN_EOF

# ---------------------------------------------------------------------------
# 5. Write install.sh into the package
# ---------------------------------------------------------------------------
cat > "${STAGE}/install.sh" <<'INSTALL_EOF'
#!/bin/bash
# ============================================================================
#  JS8FastChat installer for Linux
#
#  Run:   ./install.sh
#
#  Installs to your own home folder. No root and no sudo needed.
#  Nothing is written outside your home folder.
# ============================================================================

set -e

APPDIR="${HOME}/.local/lib/JS8FastChat"
MENUDIR="${HOME}/.local/share/applications"
DESKDIR="${HOME}/Desktop"
SRC="$(cd "$(dirname "$0")" && pwd)"

# FastChat's own settings live here. constants.py computes the same path.
DATADIR="${HOME}/.local/share/JS8MapChat/FastChat"

echo "============================================================"
echo " Installing JS8FastChat"
echo "============================================================"
echo ""

# --- never install on top of the app's own data folder ----------------------
if [ "${APPDIR}" = "${DATADIR}" ]; then
    echo "STOP: the install folder and the data folder are the same."
    echo "      Nothing has been installed."
    exit 1
fi

# --- check the package is complete before touching anything -----------------
if [ ! -f "${SRC}/JS8FastChat" ]; then
    echo "STOP: the JS8FastChat program is missing from this folder."
    echo "      Unpack the whole .tar.gz and run install.sh from inside it."
    exit 1
fi

# --- is JS8Map installed? ---------------------------------------------------
#     FastChat cannot do much on its own: callsign lookups read the database
#     JS8Map's wizard downloads, and every cross-app button needs JS8Map
#     running. Installing FastChat alone leaves an app where most things
#     silently do nothing, which reads as a broken program.
#
#     This is a WARNING, not a stop. Installing FastChat first and JS8Map
#     afterwards is a perfectly reasonable order.
JS8MAP_FOUND=0
for probe in "${HOME}/.local/lib/JS8Map/JS8Map" \
             "${HOME}/.local/share/JS8Map/ham_map_config.json" \
             "${MENUDIR}/js8map.desktop"; do
    if [ -e "$probe" ]; then
        JS8MAP_FOUND=1
        break
    fi
done

if [ "$JS8MAP_FOUND" -eq 0 ]; then
    echo "------------------------------------------------------------"
    echo " NOTE: JS8Map does not appear to be installed on this machine."
    echo ""
    echo " JS8FastChat needs it. Callsign lookups read the database that"
    echo " JS8Map's first-run wizard downloads, and the buttons that talk"
    echo " to the map need it running."
    echo ""
    echo " FastChat will still install and open, but until JS8Map is here"
    echo " those parts will do nothing -- with no error message."
    echo ""
    echo " Install JS8Map too, then start JS8Call, JS8Map and FastChat."
    echo "------------------------------------------------------------"
    echo ""
fi

# --- copy the program -------------------------------------------------------
mkdir -p "${APPDIR}"
cp "${SRC}/JS8FastChat" "${APPDIR}/JS8FastChat"
chmod 755 "${APPDIR}/JS8FastChat"

# the twin record, kept beside the program so it can be compared later
if [ -f "${SRC}/TWIN_shared_resolver.sha256" ]; then
    cp "${SRC}/TWIN_shared_resolver.sha256" "${APPDIR}/TWIN_shared_resolver.sha256"
fi

ICON_LINE=""
if [ -f "${SRC}/js8fastchat.png" ]; then
    cp "${SRC}/js8fastchat.png" "${APPDIR}/js8fastchat.png"
    ICON_LINE="Icon=${APPDIR}/js8fastchat.png"
fi

echo "Installed to: ${APPDIR}"

# --- build the launcher -----------------------------------------------------
make_launcher () {
    TARGET="$1"
    cat > "${TARGET}" <<DESKTOP_EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=JS8FastChat
GenericName=JS8Call Fast Chat
Comment=Fast messaging companion for JS8Call and JS8Map
Exec="${APPDIR}/JS8FastChat"
Path=${APPDIR}
${ICON_LINE}
Terminal=false
Categories=Network;HamRadio;
StartupNotify=true
StartupWMClass=Js8fastchat
DESKTOP_EOF
    chmod +x "${TARGET}"
}

# applications menu
mkdir -p "${MENUDIR}"
if [ -f "${MENUDIR}/js8fastchat.desktop" ]; then
    cp "${MENUDIR}/js8fastchat.desktop" "${MENUDIR}/js8fastchat.desktop.bak"
    echo "An existing menu entry was saved as js8fastchat.desktop.bak"
fi
make_launcher "${MENUDIR}/js8fastchat.desktop"

# desktop icon
if [ -d "${DESKDIR}" ]; then
    if [ -f "${DESKDIR}/JS8FastChat.desktop" ]; then
        cp "${DESKDIR}/JS8FastChat.desktop" "${DESKDIR}/JS8FastChat.desktop.bak"
        echo "An existing desktop icon was saved as JS8FastChat.desktop.bak"
    fi
    make_launcher "${DESKDIR}/JS8FastChat.desktop"
    gio set "${DESKDIR}/JS8FastChat.desktop" metadata::trusted true 2>/dev/null || true
fi

update-desktop-database "${MENUDIR}" 2>/dev/null || true

# --- prove what actually landed ---------------------------------------------
echo ""
echo "------------------------------------------------------------"
echo " Contents of ${APPDIR}"
echo "------------------------------------------------------------"
ls -1 "${APPDIR}"
echo "------------------------------------------------------------"
echo ""

if [ -x "${APPDIR}/JS8FastChat" ]; then
    echo "DONE. JS8FastChat is installed."
    echo ""
    echo "Start it from the applications menu, from the desktop icon,"
    echo "or by running:  ${APPDIR}/JS8FastChat"
    echo ""
    echo "Start order:  JS8Call first, then JS8Map, then JS8FastChat."
    echo ""
    echo "In JS8Call, open File > Settings > Reporting and set"
    echo "TCP Max Connections to 4. At 1 there are not enough"
    echo "connections for both JS8Map and FastChat."
else
    echo "WARNING: the install finished but the folder above does not look right."
fi
echo ""
INSTALL_EOF
chmod 755 "${STAGE}/install.sh"

# ---------------------------------------------------------------------------
# 6. Write uninstall.sh into the package
# ---------------------------------------------------------------------------
cat > "${STAGE}/uninstall.sh" <<'UNINSTALL_EOF'
#!/bin/bash
# ============================================================================
#  JS8FastChat uninstaller for Linux
#
#  Run:   ./uninstall.sh
#
#  Removes the installed program and its launchers.
#  Your settings and message history are NOT touched.
# ============================================================================

set -e

APPDIR="${HOME}/.local/lib/JS8FastChat"
MENUDIR="${HOME}/.local/share/applications"
DESKDIR="${HOME}/Desktop"
DATADIR="${HOME}/.local/share/JS8MapChat/FastChat"

echo "This will remove:"
echo "    ${APPDIR}"
echo "    ${MENUDIR}/js8fastchat.desktop"
echo "    ${DESKDIR}/JS8FastChat.desktop"
echo ""
echo "Your settings and message history are in a separate folder:"
echo "    ${DATADIR}"
echo "That folder will NOT be touched."
echo ""
echo "JS8Map is a separate program and is NOT removed by this."
echo ""
printf "Type YES to continue: "
read -r ANSWER

# Accept y / yes / YES and anything between, same as JS8Map's uninstaller.
case "$ANSWER" in
    [Yy]|[Yy][Ee][Ss]) ;;
    *)
        echo "Nothing was removed."
        exit 0
        ;;
esac

rm -rf "${APPDIR}"
rm -f "${MENUDIR}/js8fastchat.desktop"
rm -f "${DESKDIR}/JS8FastChat.desktop"
update-desktop-database "${MENUDIR}" 2>/dev/null || true

echo ""
echo "DONE. JS8FastChat has been removed."
echo ""
UNINSTALL_EOF
chmod 755 "${STAGE}/uninstall.sh"

# ---------------------------------------------------------------------------
# 7. Write the operator's read-me
# ---------------------------------------------------------------------------
cat > "${STAGE}/README-INSTALL.txt" <<'README_EOF'
JS8FastChat for Linux
=====================

YOU NEED JS8MAP TOO
-------------------
JS8FastChat is a companion to JS8Map, not a standalone program. Callsign
lookups read the database that JS8Map's first-run wizard downloads, and the
buttons that talk to the map need JS8Map running.

Install JS8Map first if you have not already. It is a separate download.


TO INSTALL
----------
1. Open a terminal in this folder.
2. Type:    ./install.sh
3. Press Enter.

That is all. No root password is needed. Everything is installed inside
your own home folder.

When it finishes you will have JS8FastChat in your applications menu and
an icon on your desktop.


BEFORE YOU RUN IT
-----------------
In JS8Call, open File > Settings > Reporting and set TCP Max Connections
to 4. JS8Call ships with this set to 1, and at 1 there are not enough
connections to go round -- JS8Map takes the only one and FastChat gets
nothing.


START ORDER
-----------
    1. JS8Call
    2. JS8Map
    3. JS8FastChat

JS8Map writes the shared mailbox file that FastChat looks for when it
starts, so starting FastChat first can leave the two out of step.


IF THE BUTTONS THAT TALK TO JS8MAP DO NOTHING
---------------------------------------------
Check that both programs are the same version. The two apps share a file
called shared_resolver.py which decides where their shared mailbox lives,
and if the two copies differ they use different folders -- with no error
message on either side.

This package records its copy's fingerprint in the file
TWIN_shared_resolver.sha256, installed beside the program. Compare it with
JS8Map's copy if you suspect a mismatch.


TO REMOVE IT
------------
Open a terminal in this folder and type:    ./uninstall.sh

Your settings and message history are left alone. JS8Map is not touched.
README_EOF

# ---------------------------------------------------------------------------
# 8. Make the tarball
# ---------------------------------------------------------------------------
rm -f "${PROJECT_DIR}/${PKGNAME}.tar.gz"
cd "${PROJECT_DIR}/pkgbuild"
tar czf "${PROJECT_DIR}/${PKGNAME}.tar.gz" "${PKGNAME}"
cd "${PROJECT_DIR}"
rm -rf "${PROJECT_DIR}/pkgbuild"

# ---------------------------------------------------------------------------
# 9. Prove what is in it. Last thing printed, so it cannot be missed.
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo " WHAT IS INSIDE THE PACKAGE"
echo "============================================================"
tar tzf "${PROJECT_DIR}/${PKGNAME}.tar.gz" | sed 's|^|  |'
echo ""
echo "============================================================"
echo " PACKAGE BUILT"
echo "============================================================"
echo ""
ls -lh "${PROJECT_DIR}/${PKGNAME}.tar.gz"
echo ""
sha256sum "${PROJECT_DIR}/${PKGNAME}.tar.gz"
echo ""
echo "Twin file fingerprint recorded in this package:"
echo "  ${TWIN_SUM}"
echo ""
echo "Full path:"
echo "  ${PROJECT_DIR}/${PKGNAME}.tar.gz"
echo ""
