#!/bin/bash
# make_buildbox_v2.sh
# Rebuilds the Ubuntu 20.04 (glibc 2.31) build box with ONE correction:
# Python is now compiled with --enable-shared, which PyInstaller requires.
#
# The system-package layers are unchanged, so podman reuses them from the
# first build and only the Python step is redone.

set -u

BOXDIR="$HOME/JS8Map_BuildBox"
IMAGE="js8map-build:20.04"

echo "=================================================="
echo " JS8Map build box - REBUILD with shared Python"
echo " Started: $(date)"
echo "=================================================="
echo

mkdir -p "$BOXDIR" || { echo "FAIL: cannot create $BOXDIR"; exit 1; }
cd "$BOXDIR" || exit 1

echo "--- writing the corrected recipe file ---"
cat > "$BOXDIR/Containerfile" << 'CONTAINERFILE_END'
FROM docker.io/library/ubuntu:20.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC

# Base system tools and every library Python 3.12 needs to exist here.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl wget gnupg software-properties-common \
        build-essential pkg-config file xz-utils binutils \
        zlib1g-dev libssl-dev libffi-dev libsqlite3-dev \
        libbz2-dev liblzma-dev libreadline-dev libncurses5-dev \
        uuid-dev tk-dev tcl-dev libgdbm-dev \
    && rm -rf /var/lib/apt/lists/*

# Preferred route: the deadsnakes archive carries a ready-made Python 3.12
# for 20.04, including its tkinter package. Failure here is not fatal.
RUN set -x; \
    if add-apt-repository -y ppa:deadsnakes/ppa \
       && apt-get update \
       && apt-get install -y --no-install-recommends \
            python3.12 python3.12-dev python3.12-venv python3.12-tk; then \
        echo "deadsnakes" > /opt/python_origin.txt; \
    else \
        echo "source" > /opt/python_origin.txt; \
    fi; \
    rm -rf /var/lib/apt/lists/*; \
    true

# Fallback route: compile 3.12.3 here.
# --enable-shared is REQUIRED. PyInstaller embeds libpython into the program
# it builds; a statically-built Python cannot be packaged at all. The rpath
# and ldconfig lines let the freshly built interpreter find its own library.
RUN set -eux; \
    if ! command -v python3.12 >/dev/null 2>&1; then \
        cd /tmp; \
        curl -fsSLO https://www.python.org/ftp/python/3.12.3/Python-3.12.3.tgz; \
        tar xf Python-3.12.3.tgz; \
        cd Python-3.12.3; \
        ./configure --prefix=/usr/local \
                    --enable-shared \
                    --with-ensurepip=install \
                    LDFLAGS="-Wl,-rpath,/usr/local/lib" >/dev/null; \
        make -j"$(nproc)" >/dev/null; \
        make altinstall >/dev/null; \
        echo "/usr/local/lib" > /etc/ld.so.conf.d/python-local.conf; \
        ldconfig; \
        cd /; \
        rm -rf /tmp/Python-3.12.3 /tmp/Python-3.12.3.tgz; \
    fi

# Prove the shared library exists and tkinter imports, before going further.
RUN set -eux; \
    PY="$(command -v python3.12)"; \
    ln -sf "$PY" /usr/local/bin/python3; \
    ln -sf "$PY" /usr/local/bin/python; \
    ldconfig; \
    python3 -c "import sysconfig,sys; \
assert sysconfig.get_config_var('Py_ENABLE_SHARED'), 'PYTHON IS NOT SHARED'; \
print('shared library: yes')"; \
    python3 -c "import tkinter; print('tkinter OK, Tk', tkinter.TkVersion)"

# PyInstaller pinned to the version Acer1 already builds with.
RUN set -eux; \
    python3 -m ensurepip --upgrade || true; \
    python3 -m pip install --no-cache-dir --upgrade pip setuptools wheel; \
    python3 -m pip install --no-cache-dir pyinstaller==6.20.0

WORKDIR /src

# Final self-check, baked in so a bad box cannot be created.
RUN set -eux; \
    ldd --version | head -n 1; \
    python3 --version; \
    python3 -c "import sysconfig; print('Py_ENABLE_SHARED =', sysconfig.get_config_var('Py_ENABLE_SHARED'))"; \
    python3 -c "import tkinter, sqlite3, ssl; print('modules OK')"; \
    python3 -m PyInstaller --version; \
    command -v pyinstaller || echo "console script not on PATH (a shim is used at build time)"
CONTAINERFILE_END

if [ ! -s "$BOXDIR/Containerfile" ]; then
    echo "FAIL: recipe file was not written"
    exit 1
fi
echo "Recipe written."
echo

echo "=================================================="
echo " REBUILDING - the system-package steps are cached,"
echo " so the wait is the Python compile only."
echo "=================================================="
echo

podman build -t "$IMAGE" -f "$BOXDIR/Containerfile" "$BOXDIR"
BUILD_RC=$?

echo
if [ "$BUILD_RC" -ne 0 ]; then
    echo "=================================================="
    echo " BUILD FAILED (exit code $BUILD_RC)"
    echo " Send back the STEP line above and the lines around it."
    echo "=================================================="
    exit 1
fi

echo "=================================================="
echo " VERIFYING THE FINISHED BOX"
echo "=================================================="
echo

GLIBC=$(podman run --rm "$IMAGE" ldd --version 2>/dev/null | head -n 1 | awk '{print $NF}')
PYVER=$(podman run --rm "$IMAGE" python3 --version 2>&1)
TKVER=$(podman run --rm "$IMAGE" python3 -c "import tkinter; print('present, Tk ' + str(tkinter.TkVersion))" 2>&1)
PIVER=$(podman run --rm "$IMAGE" python3 -m PyInstaller --version 2>&1 | tail -n 1)
SHARED=$(podman run --rm "$IMAGE" python3 -c "import sysconfig; print(sysconfig.get_config_var('Py_ENABLE_SHARED'))" 2>&1)

echo "glibc         : $GLIBC"
echo "python        : $PYVER"
echo "shared python : $SHARED   (must be 1)"
echo "tkinter       : $TKVER"
echo "pyinstaller   : $PIVER"
echo
podman images "$IMAGE"
echo

FAILED=0
case "$GLIBC"  in 2.31*)   ;; *) echo "WRONG: glibc should be 2.31, got '$GLIBC'"; FAILED=1;; esac
case "$PYVER"  in *3.12*)  ;; *) echo "WRONG: python should be 3.12, got '$PYVER'"; FAILED=1;; esac
case "$SHARED" in 1)       ;; *) echo "WRONG: python is not shared, got '$SHARED'"; FAILED=1;; esac
case "$TKVER"  in *present*) ;; *) echo "WRONG: tkinter not importable"; FAILED=1;; esac
case "$PIVER"  in 6.20.0*) ;; *) echo "WRONG: pyinstaller should be 6.20.0, got '$PIVER'"; FAILED=1;; esac

echo "=================================================="
if [ "$FAILED" -eq 0 ]; then
    echo " BUILD BOX READY"
else
    echo " BUILD BOX BUILT BUT DID NOT MATCH - see WRONG lines above"
fi
echo " Finished: $(date)"
echo "=================================================="
