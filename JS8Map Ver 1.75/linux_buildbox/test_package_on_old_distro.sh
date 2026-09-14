#!/bin/bash
# test_package_on_old_distro.sh <tarball> <AppName>
#
# Installs and runs ANY of the JS8MapChat Linux packages inside a bare Ubuntu
# 20.04 system (glibc 2.31) with an empty home folder - the closest thing to a
# stranger's machine without borrowing one.
#
# Nothing of yours is used: not your settings, not your databases, not your
# installed copies. The container gets a scratch home folder and is discarded.
#
#   bash test_package_on_old_distro.sh ~/Applications/JS8FastChat/JS8FastChat_v1.75_linux.tar.gz JS8FastChat
#   bash test_package_on_old_distro.sh ~/Applications/JS8Map/JS8Map_v1.75_linux.tar.gz JS8Map

set -u

TESTIMAGE="js8map-test:20.04"
BOXDIR="$HOME/JS8Map_BuildBox"

TGZ="${1:-}"
APP="${2:-}"

if [ -z "$TGZ" ] || [ -z "$APP" ]; then
    echo "Usage: bash $0 <path-to-tarball> <AppName>"
    echo "  e.g. bash $0 ~/Applications/JS8FastChat/JS8FastChat_v1.75_linux.tar.gz JS8FastChat"
    exit 1
fi

SCRATCH="$BOXDIR/testhome_${APP}"

echo "=================================================="
echo " ${APP} - install and run on a bare Ubuntu 20.04"
echo " Started: $(date)"
echo "=================================================="
echo

if [ ! -f "$TGZ" ]; then
    echo "STOP: package not found at:"
    echo "      $TGZ"
    exit 1
fi
echo "Package found:"
ls -lh "$TGZ"
sha256sum "$TGZ"
echo

if [ -z "${DISPLAY:-}" ]; then
    echo "STOP: no graphical session detected (DISPLAY is empty)."
    echo "      Run this from a terminal on the desktop, not over SSH."
    exit 1
fi
echo "Display: $DISPLAY"

XAUTH="${XAUTHORITY:-$HOME/.Xauthority}"
[ -f "$XAUTH" ] || XAUTH=""
echo

# --- the bare 20.04 test system --------------------------------------------
if podman image exists "$TESTIMAGE" 2>/dev/null; then
    echo "Bare 20.04 test system already built: $TESTIMAGE"
else
    echo "--- building the bare 20.04 test system (once, ~1 minute) ---"
    mkdir -p "$BOXDIR"
    cat > "$BOXDIR/Containerfile.test" << 'TESTFILE_END'
FROM docker.io/library/ubuntu:20.04
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC
# Only what a normal desktop already provides. Deliberately NOT python,
# NOT tkinter, NOT any build tool.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libx11-6 libxext6 libxrender1 libxft2 libxss1 libxinerama1 \
        libfontconfig1 fonts-dejavu-core \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*
TESTFILE_END
    podman build -t "$TESTIMAGE" -f "$BOXDIR/Containerfile.test" "$BOXDIR" || {
        echo "STOP: could not build the test system."
        exit 1
    }
fi
echo

# --- a completely empty home folder ----------------------------------------
rm -rf "$SCRATCH"
mkdir -p "$SCRATCH"
cp "$TGZ" "$SCRATCH/package.tar.gz"
echo "Scratch home folder: $SCRATCH"
echo

XHOST_CHANGED=0
if command -v xhost >/dev/null 2>&1; then
    xhost +local: >/dev/null 2>&1 && XHOST_CHANGED=1
fi

XARGS=""
if [ -n "$XAUTH" ]; then
    XARGS="-v $XAUTH:/tmp/.Xauth:ro -e XAUTHORITY=/tmp/.Xauth"
fi

echo "=================================================="
echo " UNPACKING AND INSTALLING, AS A STRANGER WOULD"
echo "=================================================="
echo

# shellcheck disable=SC2086
podman run --rm -it \
    --userns=keep-id \
    --net=host \
    -e DISPLAY="$DISPLAY" \
    $XARGS \
    -v /tmp/.X11-unix:/tmp/.X11-unix \
    -v "$SCRATCH":/home/testop:Z \
    -e HOME=/home/testop \
    -e APPNAME="$APP" \
    -w /home/testop \
    "$TESTIMAGE" \
    /bin/bash -c '
        set -e
        echo "--- this system ---"
        ldd --version | head -n 1
        echo -n "python present?  : "
        command -v python3 || echo "no - correct, a stranger has none"
        echo -n "tkinter package? : "
        ls -d /usr/lib/python3*/tkinter 2>/dev/null || echo "no - correct"
        echo
        echo "--- unpacking ---"
        tar xzf package.tar.gz
        cd "$(find . -maxdepth 1 -type d -name "${APPNAME}_*" | head -n 1)"
        ls -1
        echo
        echo "--- running install.sh ---"
        ./install.sh
        echo
        echo "=================================================="
        echo " LAUNCHING. A window should appear on your screen."
        echo " CLOSE THE WINDOW when you have seen it."
        echo "=================================================="
        "$HOME/.local/lib/${APPNAME}/${APPNAME}" || echo "EXIT CODE: $?"
        echo
        echo "--- window closed ---"
    '
RC=$?

if [ "$XHOST_CHANGED" -eq 1 ]; then
    xhost -local: >/dev/null 2>&1
fi

echo
echo "=================================================="
echo " RESULT"
echo "=================================================="
if [ "$RC" -eq 0 ]; then
    echo " ${APP} installed and launched on a bare Ubuntu 20.04"
    echo " system with no Python and no tkinter, from an empty"
    echo " home folder."
else
    echo " The run ended with code $RC - send the output above back."
fi
echo
echo " Nothing of yours was touched. Scratch folder:"
echo "   rm -rf $SCRATCH"
echo
echo " Finished: $(date)"
echo "=================================================="
