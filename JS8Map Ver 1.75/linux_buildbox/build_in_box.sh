#!/bin/bash
# build_in_box.sh
# Rebuilds JS8Map inside the Ubuntu 20.04 / glibc 2.31 box.
#
# Works on a SEPARATE COPY of the source. The real source folder, its dist/
# folder and the proven 28 August tarball are never written to.

set -u

IMAGE="js8map-build:20.04"
SRC_REAL="$HOME/Applications/JS8Map"
BOXDIR="$HOME/JS8Map_BuildBox"
SRC_COPY="$BOXDIR/src"

echo "=================================================="
echo " JS8Map rebuild inside the 20.04 box"
echo " Started: $(date)"
echo "=================================================="
echo

# --- 1. the box must exist --------------------------------------------------
if ! podman image exists "$IMAGE" 2>/dev/null; then
    echo "STOP: the build box '$IMAGE' was not found."
    echo "      Run make_buildbox.sh first."
    exit 1
fi
echo "Build box found: $IMAGE"

# --- 2. the real source must exist and is only ever READ --------------------
if [ ! -f "$SRC_REAL/JS8Map.py" ]; then
    echo "STOP: $SRC_REAL does not look like the JS8Map source folder."
    exit 1
fi
echo "Source folder found: $SRC_REAL"
echo

# --- 3. make a clean copy to work in ----------------------------------------
echo "--- copying the source to a working folder ---"
rm -rf "$SRC_COPY"
mkdir -p "$SRC_COPY"
cp -a "$SRC_REAL/." "$SRC_COPY/" || { echo "STOP: copy failed"; exit 1; }

# Drop everything that is output rather than source, so nothing stale is
# mistaken for a fresh result, and the old tarball cannot be confused with
# the new one.
rm -rf "$SRC_COPY/dist" "$SRC_COPY/build" "$SRC_COPY/pkgbuild"
rm -f  "$SRC_COPY"/*.tar.gz
rm -f  "$SRC_COPY"/*.PRE0828
rm -f  "$SRC_COPY/ham_map.html"

echo "Working copy: $SRC_COPY"
echo "Files copied: $(find "$SRC_COPY" -maxdepth 1 -type f | wc -l)"
echo

# --- 4. build and package, inside the box -----------------------------------
# --userns=keep-id  so files come out owned by you, not by a container user
# HOME=/tmp         PyInstaller wants somewhere to put its cache
# A pyinstaller shim is placed on PATH in case only the module form exists.
echo "=================================================="
echo " BUILDING INSIDE THE BOX - takes a few minutes"
echo "=================================================="
echo

podman run --rm \
    --userns=keep-id \
    -v "$SRC_COPY":/src:Z \
    -w /src \
    -e HOME=/tmp \
    "$IMAGE" \
    /bin/bash -c '
        set -e
        if ! command -v pyinstaller >/dev/null 2>&1; then
            mkdir -p /tmp/bin
            printf "#!/bin/sh\nexec python3 -m PyInstaller \"\$@\"\n" > /tmp/bin/pyinstaller
            chmod +x /tmp/bin/pyinstaller
            export PATH="/tmp/bin:$PATH"
        fi
        echo "--- inside the box ---"
        ldd --version | head -n 1
        python3 --version
        pyinstaller --version
        echo "----------------------"
        echo
        chmod +x ./build_js8map_linux.sh ./make_js8map_package.sh
        ./build_js8map_linux.sh
        echo
        echo "=================================================="
        echo " PACKAGING INSIDE THE BOX"
        echo "=================================================="
        ./make_js8map_package.sh
    '
RC=$?

echo
if [ "$RC" -ne 0 ]; then
    echo "=================================================="
    echo " BUILD FAILED INSIDE THE BOX (exit code $RC)"
    echo " Send back the last 20 lines above this."
    echo "=================================================="
    exit 1
fi

# --- 5. report, and compare against the proven build ------------------------
NEW_EXE="$SRC_COPY/dist/JS8Map"
NEW_TGZ="$SRC_COPY/JS8Map_v1.75.1_linux.tar.gz"
OLD_EXE="$SRC_REAL/dist/JS8Map"
OLD_TGZ="$SRC_REAL/JS8Map_v1.75.1_linux.tar.gz"

echo "=================================================="
echo " RESULT"
echo "=================================================="
echo
echo "--- NEW, built on glibc 2.31 ---"
[ -f "$NEW_EXE" ] && ls -l "$NEW_EXE" || echo "  MISSING: $NEW_EXE"
[ -f "$NEW_TGZ" ] && ls -lh "$NEW_TGZ" || echo "  MISSING: $NEW_TGZ"
[ -f "$NEW_TGZ" ] && sha256sum "$NEW_TGZ"
echo
echo "--- OLD, built on glibc 2.39, untouched ---"
[ -f "$OLD_EXE" ] && ls -l "$OLD_EXE" || echo "  (no old build present)"
[ -f "$OLD_TGZ" ] && sha256sum "$OLD_TGZ"
echo
echo "--- what the stub alone reports (MISLEADING - see below) ---"
if [ -f "$NEW_EXE" ]; then
    objdump -T "$NEW_EXE" 2>/dev/null \
        | grep -o 'GLIBC_[0-9.]*' | sort -V -u | tail -n 3
    echo "  (the stub is not the real floor - the bundled libraries are,"
    echo "   and those are measured in the next step, with the app running)"
fi
echo
echo "--- contents of the new package ---"
[ -f "$NEW_TGZ" ] && tar tzf "$NEW_TGZ" | sed 's|^|  |'
echo
echo "=================================================="
if [ -f "$NEW_EXE" ] && [ -f "$NEW_TGZ" ]; then
    echo " REBUILD COMPLETE"
    echo " New program : $NEW_EXE"
    echo " New package : $NEW_TGZ"
else
    echo " REBUILD DID NOT PRODUCE BOTH OUTPUTS - see MISSING lines above"
fi
echo " Finished: $(date)"
echo "=================================================="
