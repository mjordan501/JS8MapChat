#!/bin/bash
# ============================================================================
#  make_js8map_package.sh
#
#  Makes the shippable Linux package for JS8Map.
#
#  Run this on the BUILD machine, from the project folder, AFTER a successful
#  ./build_js8map_linux.sh
#
#  Output:  JS8Map_v1.75_linux.tar.gz   in the project folder
#
#  That single .tar.gz is what goes to another Linux operator. They unpack it
#  and run install.sh. No root, no sudo, no package manager.
# ============================================================================

set -e

VERSION="1.75"
PKGNAME="JS8Map_v${VERSION}_linux"

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"
STAGE="${PROJECT_DIR}/pkgbuild/${PKGNAME}"

echo "============================================================"
echo " JS8Map Linux package builder"
echo " Project folder: ${PROJECT_DIR}"
echo "============================================================"
echo ""

# ---------------------------------------------------------------------------
# 1. The built program must exist
# ---------------------------------------------------------------------------
if [ ! -f "${PROJECT_DIR}/dist/JS8Map" ]; then
    echo "STOP: dist/JS8Map was not found."
    echo "      Build it first:  ./build_js8map_linux.sh"
    exit 1
fi
echo "Found the built program: dist/JS8Map"

# ---------------------------------------------------------------------------
# 2. All six map files must already be beside it in dist/
#    A missing one here gives a BLANK map on the operator's machine, so this
#    is checked before anything is packed, not after.
# ---------------------------------------------------------------------------
MAPFILES="leaflet.js leaflet.css js8map_borders.json js8map_admin1.json js8map_lakes.json js8map_places.json"

MISSING=""
for f in $MAPFILES; do
    if [ ! -f "${PROJECT_DIR}/dist/${f}" ]; then
        MISSING="${MISSING} ${f}"
    fi
done

if [ -n "$MISSING" ]; then
    echo ""
    echo "STOP: these map files are missing from dist/ :"
    for f in $MISSING; do echo "        ${f}"; done
    echo ""
    echo "      Re-run ./build_js8map_linux.sh and check its last lines."
    exit 1
fi
echo "Found all six map files in dist/"

# ---------------------------------------------------------------------------
# 2b. build_canadian_db.py must be packed too.
#     js8map_fcc.py runs this script to build the Canadian half of ham.db, and
#     it looks for it beside the executable (_EXE_DIR). On Windows JS8Map.iss
#     ships it to {app}; nothing was doing the equivalent here, so the wizard's
#     Canadian step failed on every Linux install -- and the wizard reports the
#     failure as "Download failed", so it looked like a network fault. Checked
#     before packing, like the map files, because a miss is invisible until an
#     operator reaches step 2 of the wizard.
# ---------------------------------------------------------------------------
if [ ! -f "${PROJECT_DIR}/build_canadian_db.py" ]; then
    echo ""
    echo "STOP: build_canadian_db.py was not found in ${PROJECT_DIR}"
    echo "      Without it the Canadian database step cannot work on the"
    echo "      operator's machine. Nothing has been packaged."
    exit 1
fi
echo "Found the Canadian database builder: build_canadian_db.py"

# ---------------------------------------------------------------------------
# 3. Find the icon by reading it out of the existing JS8Map.desktop
# ---------------------------------------------------------------------------
ICON_SRC=""
if [ -f "${PROJECT_DIR}/JS8Map.desktop" ]; then
    ICON_LINE="$(grep -m1 '^Icon=' "${PROJECT_DIR}/JS8Map.desktop" | cut -d'=' -f2- || true)"
    if [ -n "$ICON_LINE" ] && [ -f "$ICON_LINE" ]; then
        ICON_SRC="$ICON_LINE"
    fi
fi

if [ -z "$ICON_SRC" ]; then
    for guess in "${PROJECT_DIR}/JS8Map_icon.png" "${PROJECT_DIR}/js8map.png" \
                 "${PROJECT_DIR}/JS8Map.png" "${PROJECT_DIR}/icon.png" \
                 "${PROJECT_DIR}/assets/js8map.png" "${PROJECT_DIR}/icons/js8map.png"; do
        if [ -f "$guess" ]; then
            ICON_SRC="$guess"
            break
        fi
    done
fi

if [ -n "$ICON_SRC" ]; then
    echo "Found the icon: ${ICON_SRC}"
else
    echo "NOTE: no icon file found. The package will still build and install,"
    echo "      but the launcher will show a generic icon."
fi

# ---------------------------------------------------------------------------
# 4. Stage everything
# ---------------------------------------------------------------------------
rm -rf "${PROJECT_DIR}/pkgbuild"
mkdir -p "${STAGE}"

cp "${PROJECT_DIR}/dist/JS8Map" "${STAGE}/JS8Map"
chmod 755 "${STAGE}/JS8Map"

for f in $MAPFILES; do
    cp "${PROJECT_DIR}/dist/${f}" "${STAGE}/${f}"
done

cp "${PROJECT_DIR}/build_canadian_db.py" "${STAGE}/build_canadian_db.py"

if [ -n "$ICON_SRC" ]; then
    cp "$ICON_SRC" "${STAGE}/js8map.png"
fi

# ---------------------------------------------------------------------------
# 5. Write install.sh into the package
#
#    Installs into the operator's own home folder:
#        ~/.local/lib/JS8Map/
#    No root. Writable. The six map files land BESIDE the executable there,
#    which is the same arrangement JS8Map.iss makes on Windows.
# ---------------------------------------------------------------------------
cat > "${STAGE}/install.sh" <<'INSTALL_EOF'
#!/bin/bash
# ============================================================================
#  JS8Map installer for Linux
#
#  Run:   ./install.sh
#
#  Installs to your own home folder. No root and no sudo needed.
#  Nothing is written outside your home folder.
# ============================================================================

set -e

APPDIR="${HOME}/.local/lib/JS8Map"
MENUDIR="${HOME}/.local/share/applications"
DESKDIR="${HOME}/Desktop"
SRC="$(cd "$(dirname "$0")" && pwd)"

MAPFILES="leaflet.js leaflet.css js8map_borders.json js8map_admin1.json js8map_lakes.json js8map_places.json"

DATADIR="${HOME}/.local/share/JS8Map"

echo "============================================================"
echo " Installing JS8Map"
echo "============================================================"
echo ""

# --- never install on top of the app's own data folder ----------------------
#     ~/.local/share/JS8Map is where JS8Map keeps settings, databases and the
#     browser scratch folder. The program must not be unpacked into it.
if [ "${APPDIR}" = "${DATADIR}" ]; then
    echo "STOP: the install folder and the data folder are the same."
    echo "      Nothing has been installed."
    exit 1
fi
for marker in ham_map_config.json js8_spots.db js8_relay.db map_window_profile; do
    if [ -e "${APPDIR}/${marker}" ]; then
        echo "STOP: ${APPDIR}"
        echo "      already holds JS8Map settings or databases."
        echo "      The program must not be installed on top of them."
        echo "      Nothing has been installed."
        exit 1
    fi
done

# --- check the package is complete before touching anything -----------------
if [ ! -f "${SRC}/JS8Map" ]; then
    echo "STOP: the JS8Map program is missing from this folder."
    echo "      Unpack the whole .tar.gz and run install.sh from inside it."
    exit 1
fi

MISSING=""
for f in $MAPFILES; do
    if [ ! -f "${SRC}/${f}" ]; then
        MISSING="${MISSING} ${f}"
    fi
done
# build_canadian_db.py is required too -- the Canadian step of the first-run
# wizard runs it, and looks for it beside the program.
if [ ! -f "${SRC}/build_canadian_db.py" ]; then
    MISSING="${MISSING} build_canadian_db.py"
fi
if [ -n "$MISSING" ]; then
    echo "STOP: this package is incomplete. Missing:"
    for f in $MISSING; do echo "        ${f}"; done
    echo "      Nothing has been installed."
    exit 1
fi

# --- copy the program and the six map files, side by side -------------------
mkdir -p "${APPDIR}"
cp "${SRC}/JS8Map" "${APPDIR}/JS8Map"
chmod 755 "${APPDIR}/JS8Map"

for f in $MAPFILES; do
    cp "${SRC}/${f}" "${APPDIR}/${f}"
done

# the Canadian database builder, beside the program where js8map_fcc.py looks
cp "${SRC}/build_canadian_db.py" "${APPDIR}/build_canadian_db.py"

ICON_LINE=""
if [ -f "${SRC}/js8map.png" ]; then
    cp "${SRC}/js8map.png" "${APPDIR}/js8map.png"
    ICON_LINE="Icon=${APPDIR}/js8map.png"
fi

echo "Installed to: ${APPDIR}"

# --- build the launcher -----------------------------------------------------
make_launcher () {
    TARGET="$1"
    cat > "${TARGET}" <<DESKTOP_EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=JS8Map
GenericName=JS8Call Map
Comment=Map and spot display for JS8Call
Exec="${APPDIR}/JS8Map"
Path=${APPDIR}
${ICON_LINE}
Terminal=false
Categories=Network;HamRadio;
StartupNotify=true
StartupWMClass=Js8map
DESKTOP_EOF
    chmod +x "${TARGET}"
}

# applications menu
mkdir -p "${MENUDIR}"
if [ -f "${MENUDIR}/js8map.desktop" ]; then
    cp "${MENUDIR}/js8map.desktop" "${MENUDIR}/js8map.desktop.bak"
    echo "An existing menu entry was saved as js8map.desktop.bak"
fi
make_launcher "${MENUDIR}/js8map.desktop"

# desktop icon
if [ -d "${DESKDIR}" ]; then
    if [ -f "${DESKDIR}/JS8Map.desktop" ]; then
        cp "${DESKDIR}/JS8Map.desktop" "${DESKDIR}/JS8Map.desktop.bak"
        echo "An existing desktop icon was saved as JS8Map.desktop.bak"
    fi
    make_launcher "${DESKDIR}/JS8Map.desktop"
    gio set "${DESKDIR}/JS8Map.desktop" metadata::trusted true 2>/dev/null || true
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

COUNT=0
for f in $MAPFILES; do
    if [ -f "${APPDIR}/${f}" ]; then
        COUNT=$((COUNT + 1))
    fi
done

if [ "$COUNT" -eq 6 ] && [ -x "${APPDIR}/JS8Map" ] \
   && [ -f "${APPDIR}/build_canadian_db.py" ]; then
    echo "DONE. JS8Map is installed and all six map files are beside it."
    echo ""
    echo "Start it from the applications menu, from the desktop icon,"
    echo "or by running:  ${APPDIR}/JS8Map"
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
#  JS8Map uninstaller for Linux
#
#  Run:   ./uninstall.sh
#
#  Removes the installed program and its launchers.
#  Your settings and databases are NOT touched.
# ============================================================================

set -e

APPDIR="${HOME}/.local/lib/JS8Map"
MENUDIR="${HOME}/.local/share/applications"
DESKDIR="${HOME}/Desktop"
DATADIR="${HOME}/.local/share/JS8Map"

echo "This will remove:"
echo "    ${APPDIR}"
echo "    ${MENUDIR}/js8map.desktop"
echo "    ${DESKDIR}/JS8Map.desktop"
echo ""
echo "Your settings and databases are in a separate folder:"
echo "    ${DATADIR}"
echo "That folder will NOT be touched."
echo ""
printf "Type YES to continue: "
read -r ANSWER

# Accept y / yes / YES and anything between. The old test was an exact match on
# "YES", so a lower-case "yes" silently cancelled -- safe, but every new
# operator hits it once.
case "$ANSWER" in
    [Yy]|[Yy][Ee][Ss]) ;;
    *)
        echo "Nothing was removed."
        exit 0
        ;;
esac

rm -rf "${APPDIR}"
rm -f "${MENUDIR}/js8map.desktop"
rm -f "${DESKDIR}/JS8Map.desktop"
update-desktop-database "${MENUDIR}" 2>/dev/null || true

echo ""
echo "DONE. JS8Map has been removed."
echo ""
UNINSTALL_EOF
chmod 755 "${STAGE}/uninstall.sh"

# ---------------------------------------------------------------------------
# 7. Write the operator's read-me
# ---------------------------------------------------------------------------
cat > "${STAGE}/README-INSTALL.txt" <<'README_EOF'
JS8Map for Linux
================

TO INSTALL
----------
1. Open a terminal in this folder.
2. Type:    ./install.sh
3. Press Enter.

That is all. No root password is needed. Everything is installed inside
your own home folder.

When it finishes you will have JS8Map in your applications menu and an
icon on your desktop.


BEFORE YOU RUN IT
-----------------
In JS8Call, open File > Settings > Reporting and set
TCP Max Connections to 4. JS8Call ships with this set to 1, and at 1 the
map cannot get its own connection.


THE MAP WORKS OFFLINE
---------------------
No map key is needed and no internet connection is needed for the map to
draw. Coastlines, borders, state and province lines, lakes, place names
and stations all draw with the network off. Street-level detail is the
only thing that needs a connection, and it is optional.


TO REMOVE IT
------------
Open a terminal in this folder and type:    ./uninstall.sh

Your settings and logs are left alone.
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
echo "Full path:"
echo "  ${PROJECT_DIR}/${PKGNAME}.tar.gz"
echo ""
