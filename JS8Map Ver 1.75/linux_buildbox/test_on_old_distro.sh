#!/bin/bash
# test_on_old_distro.sh
# Installs and runs the NEW JS8Map package inside a bare Ubuntu 20.04 system
# (glibc 2.31) with an empty home folder - the closest thing to a stranger's
# machine without borrowing one.
#
# Nothing of yours is used: not your settings, not your databases, not your
# installed copy. The container gets a scratch home folder and is thrown away.

set -u

TESTIMAGE="js8map-test:20.04"
BOXDIR="$HOME/JS8Map_BuildBox"
TGZ="$BOXDIR/src/JS8Map_v1.75_linux.tar.gz"
SCRATCH="$BOXDIR/testhome"

echo "=================================================="
echo " JS8Map - install and run on a bare Ubuntu 20.04"
echo " Started: $(date)"
echo "=================================================="
echo

# --- 1. the new package must exist ------------------------------------------
if [ ! -f "$TGZ" ]; then
    echo "STOP: the new package was not found at:"
    echo "      $TGZ"
    echo "      Run build_in_box.sh first."
    exit 1
fi
echo "Package found:"
ls -lh "$TGZ"
sha256sum "$TGZ"
echo

# --- 2. we need a graphical connection --------------------------------------
if [ -z "${DISPLAY:-}" ]; then
    echo "STOP: no graphical session detected (DISPLAY is empty)."
    echo "      Run this from a terminal on the Acer1 desktop, not over SSH."
    exit 1
fi
echo "Display: $DISPLAY"

XAUTH="${XAUTHORITY:-$HOME/.Xauthority}"
if [ ! -f "$XAUTH" ]; then
    echo "NOTE: no .Xauthority file found; relying on local access permission."
    XAUTH=""
fi
echo

# --- 3. build the bare test system, once -------------------------------------
# Ubuntu 20.04 with only the X libraries any desktop already has. Nothing
# else: no Python, no tkinter, no build tools. If JS8Map needs something
# that is not here, it will fail here, which is the point.
if podman image exists "$TESTIMAGE" 2>/dev/null; then
    echo "Bare 20.04 test system already built: $TESTIMAGE"
else
    echo "--- building the bare 20.04 test system (once, ~1 minute) ---"
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
RUN echo "no python here:" && (command -v python3 || echo "  correct, none")
TESTFILE_END
    podman build -t "$TESTIMAGE" -f "$BOXDIR/Containerfile.test" "$BOXDIR" || {
        echo "STOP: could not build the test system."
        exit 1
    }
fi
echo

# --- 4. a completely empty home folder --------------------------------------
rm -rf "$SCRATCH"
mkdir -p "$SCRATCH"
cp "$TGZ" "$SCRATCH/JS8Map_v1.75_linux.tar.gz"
echo "Scratch home folder: $SCRATCH"
echo "  contents: $(ls "$SCRATCH")"
echo

# --- 5. allow the container to draw on your screen --------------------------
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
    -w /home/testop \
    "$TESTIMAGE" \
    /bin/bash -c '
        set -e
        echo "--- this system ---"
        ldd --version | head -n 1
        echo -n "python present? : "
        command -v python3 || echo "no - correct, a stranger has none"
        echo -n "tkinter package? : "
        ls /usr/lib/python3*/tkinter 2>/dev/null || echo "no - correct"
        echo
        echo "--- unpacking ---"
        tar xzf JS8Map_v1.75_linux.tar.gz
        cd JS8Map_v1.75_linux
        ls -1
        echo
        echo "--- running install.sh ---"
        ./install.sh
        echo
        echo "=================================================="
        echo " LAUNCHING. The first-run wizard should appear."
        echo " Look at your screen. CLOSE THE WINDOW when done."
        echo "=================================================="
        "$HOME/.local/lib/JS8Map/JS8Map" || echo "EXIT CODE: $?"
        echo
        echo "--- window closed ---"
    '
RC=$?

# --- 6. put your screen permissions back ------------------------------------
if [ "$XHOST_CHANGED" -eq 1 ]; then
    xhost -local: >/dev/null 2>&1
fi

echo
echo "=================================================="
echo " RESULT"
echo "=================================================="
if [ "$RC" -eq 0 ]; then
    echo " The package installed and the program was launched"
    echo " on a bare Ubuntu 20.04 system with no Python and no"
    echo " tkinter, using an empty home folder."
else
    echo " The run ended with code $RC - send the output above back."
fi
echo
echo " Nothing of yours was touched. The scratch folder can be"
echo " deleted whenever you like:"
echo "   rm -rf $SCRATCH"
echo
echo " Finished: $(date)"
echo "=================================================="
