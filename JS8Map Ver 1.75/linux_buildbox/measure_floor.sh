#!/bin/bash
# measure_floor.sh
# Measures the REAL glibc requirement of a PyInstaller onefile build.
#
# The executable stub is not the answer - it reports something like 2.14 and is
# misleading. The true floor comes from the libraries the program unpacks to
# /tmp/_MEI* when it starts, and that folder is deleted the moment it closes.
# So: start it, measure while it is open, then close it.

set -u

TARGET="${1:-$HOME/JS8Map_BuildBox/src/dist/JS8Map}"

echo "=================================================="
echo " Measuring the real glibc floor"
echo " Program: $TARGET"
echo " Started: $(date)"
echo "=================================================="
echo

if [ ! -x "$TARGET" ]; then
    echo "STOP: $TARGET was not found, or is not runnable."
    exit 1
fi

if ! command -v objdump >/dev/null 2>&1; then
    echo "STOP: objdump is not installed on this machine."
    echo "      Install it with:  sudo apt install binutils"
    exit 1
fi

# --- what is already in /tmp, so we only look at the new one ----------------
BEFORE=$(ls -d /tmp/_MEI* 2>/dev/null | sort)
echo "Existing unpack folders before launch:"
if [ -z "$BEFORE" ]; then echo "  (none)"; else echo "$BEFORE" | sed 's|^|  |'; fi
echo

# --- 1. what the stub alone claims - recorded, but NOT the answer ----------
echo "--- the stub on its own (misleading, recorded for the notes) ---"
objdump -T "$TARGET" 2>/dev/null | grep -o 'GLIBC_[0-9.]*' | sort -V -u | tail -n 5 | sed 's|^|  |'
echo

# --- 2. launch it -----------------------------------------------------------
echo "--- launching the program ---"
echo "    A window will open. Leave it alone; this closes it again."
"$TARGET" >/tmp/js8map_measure.log 2>&1 &
APP_PID=$!
echo "    running as process $APP_PID"

# --- 3. wait for the unpack folder to appear --------------------------------
MEI=""
for i in $(seq 1 60); do
    sleep 1
    for d in /tmp/_MEI*; do
        [ -d "$d" ] || continue
        case "$BEFORE" in
            *"$d"*) continue ;;
        esac
        MEI="$d"
        break
    done
    [ -n "$MEI" ] && break
done

if [ -z "$MEI" ]; then
    echo
    echo "STOP: no unpack folder appeared within 60 seconds."
    echo "      The program may have failed to start. Its output:"
    echo "      ------------------------------------------------"
    tail -n 30 /tmp/js8map_measure.log 2>/dev/null | sed 's|^|      |'
    kill "$APP_PID" 2>/dev/null
    exit 1
fi

echo "    unpack folder found: $MEI"
sleep 5

# --- 4. is it actually running? ---------------------------------------------
if kill -0 "$APP_PID" 2>/dev/null; then
    echo "    the program is running - it starts on this machine"
    STARTED="yes"
else
    echo "    NOTE: the program exited early. Measuring what it unpacked anyway."
    STARTED="no"
fi
echo

# --- 5. measure every bundled library ---------------------------------------
echo "=================================================="
echo " SCANNING THE BUNDLED LIBRARIES"
echo "=================================================="
SOCOUNT=$(find "$MEI" -type f \( -name '*.so' -o -name '*.so.*' \) 2>/dev/null | wc -l)
echo "Shared libraries found: $SOCOUNT"
echo

ALLVERS=$(find "$MEI" -type f \( -name '*.so' -o -name '*.so.*' \) -print0 2>/dev/null \
          | xargs -0 -r objdump -T 2>/dev/null \
          | grep -o 'GLIBC_[0-9][0-9.]*' | sort -V -u)

echo "--- every glibc version referenced, lowest to highest ---"
echo "$ALLVERS" | sed 's|^|  |'
echo

HIGHEST=$(echo "$ALLVERS" | tail -n 1)
HNUM="${HIGHEST#GLIBC_}"

echo "--- which libraries need the highest one ($HIGHEST) ---"
find "$MEI" -type f \( -name '*.so' -o -name '*.so.*' \) -print0 2>/dev/null \
    | while IFS= read -r -d '' f; do
          if objdump -T "$f" 2>/dev/null | grep -q "$HIGHEST\b"; then
              echo "  $(basename "$f")"
          fi
      done
echo

# --- 6. close the program ---------------------------------------------------
echo "--- closing the program ---"
kill "$APP_PID" 2>/dev/null
sleep 3
kill -9 "$APP_PID" 2>/dev/null
rm -rf "$MEI" 2>/dev/null
echo "    closed."
echo

# --- 7. the verdict ---------------------------------------------------------
echo "=================================================="
echo " VERDICT"
echo "=================================================="
echo " Highest glibc required : $HNUM"
echo " Program started        : $STARTED"
echo
case "$HNUM" in
    2.31|2.3|2.2*|2.30|2.29|2.28|2.27|2.26|2.25|2.24|2.23|2.22|2.21|2.20|2.1*)
        echo " PASS - floor is $HNUM"
        echo
        echo " This will start on:"
        echo "   Linux Mint 20, 21 and 22"
        echo "   Ubuntu 20.04, 22.04, 24.04 and newer"
        echo "   Debian 11 and 12"
        echo " It was 2.38 before, which excluded all of the above except Mint 22."
        ;;
    *)
        echo " NOT THE TARGET - floor is $HNUM, expected 2.31 or lower."
        echo " Send this whole output back."
        ;;
esac
echo
echo " Finished: $(date)"
echo "=================================================="
