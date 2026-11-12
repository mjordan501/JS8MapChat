#!/bin/sh
# linux_install.sh -- first-time setup of JS8FastChat on Linux.
#
#   F="$(ls -t ~/Downloads/JS8FastChat_Ver_*_Linux.tar.gz | head -n 1)"
#   mkdir -p ~/Applications && tar -xzf "$F" -C ~/Applications
#   sh ~/Applications/"$(tar -tzf "$F" | head -n 1 | cut -d/ -f1)"/linux_install.sh
#
# The last line runs the installer in the folder that was just unpacked, never
# a guess with *, which would pick an older version sitting beside it.
#
# ~/Applications is the standard home for every JS8MapChat app on Linux. The
# program runs from wherever it was unpacked, so another folder works too.
#
# What it does:
#   1. checks for Python 3.8+ with Tk (needed), and unzip and wmctrl/xdotool
#      (recommended); if any are missing it shows the apt command and offers
#      to run it (asks for your password; nothing is installed without a yes);
#   2. offers to remove an older installed JS8FastChat (1.75.1 and earlier put
#      the program in ~/.local/lib/JS8FastChat; the first 1.76 tests used
#      ~/JS8FastChat). Settings are in ~/.local/share/JS8MapChat, not in
#      either, so removing one loses nothing;
#   3. restores the "executable" marks a Windows PC may have dropped;
#   4. adds the menu entry and desktop icon (tools/install_linux_launcher.sh);
#   5. reminds you of the JS8Call settings and how to update later.
# A newer version installs with the same three lines; this script then offers
# to remove the older copy and moves the menu entry to the new one.
# Safe to run again. Does not start the program.
set -e

say()  { printf '%s\n' "$*"; }
ask()  {
    printf '%s [y/N] ' "$1"
    a=''; { read -r a < /dev/tty; } 2>/dev/null || a=''
    case "$a" in [Yy]|[Yy][Ee][Ss]) return 0 ;; *) return 1 ;; esac
}

APP="$(cd "$(dirname "$0")" && pwd)"
[ -f "$APP/JS8FastChat.py" ] || { say "STOP: run this from the unpacked JS8FastChat folder."; exit 1; }
PY="${PYTHON:-python3}"

say "JS8FastChat -- Linux setup"
say "  folder: $APP"
say ""

# --- 1. what is needed ---------------------------------------------------------------
check() {
    NEED=''; WANT=''
    if ! command -v "$PY" >/dev/null 2>&1; then
        NEED="$NEED python3"
    elif ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
        say "  Python: $("$PY" --version 2>&1) is too old (3.8 or newer needed)."
        NEED="$NEED python3"
    fi
    if command -v "$PY" >/dev/null 2>&1 && ! "$PY" -c 'import tkinter' 2>/dev/null; then
        NEED="$NEED python3-tk"
    fi
    command -v unzip   >/dev/null 2>&1 || WANT="$WANT unzip"
    command -v wmctrl  >/dev/null 2>&1 || WANT="$WANT wmctrl"
    command -v xdotool >/dev/null 2>&1 || WANT="$WANT xdotool"
}
check
if [ -n "$NEED$WANT" ]; then
    [ -n "$NEED" ] && say "  Needed, missing:     $NEED"
    [ -n "$WANT" ] && say "  Recommended, missing:$WANT"
    [ -n "$WANT" ] && say "     (unzip: for older .zip updates;  wmctrl/xdotool: bring the JS8Map window to the front)"
    say ""
    say "  Install command:  sudo apt install$NEED$WANT"
    if command -v apt-get >/dev/null 2>&1 && ask "  Run it now?"; then
        sudo apt-get install -y $NEED $WANT || say "  (the install did not finish; see the messages above)"
        check
    fi
fi
if [ -n "$NEED" ]; then
    say ""
    say "STOP: still missing:$NEED"
    say "Install it (command above), then run this again."
    exit 1
fi
say "  Python: $("$PY" --version 2>&1) with Tk -- OK"

# --- 2. an older installed copy ----------------------------------------------------
# 1.75.1 and earlier installed a frozen build into ~/.local/lib/JS8FastChat.
# This folder replaces it. Only ever remove OUR own folder: it must hold the
# program file the old installer put there.
OLD="$HOME/.local/lib/JS8FastChat"
if [ -d "$OLD" ] && [ -f "$OLD/JS8FastChat" ]; then
    say ""
    say "  An older JS8FastChat is installed in ~/.local/lib/JS8FastChat."
    say "  This folder replaces it, and the menu entry will point here instead."
    if ask "  Remove the old copy?"; then
        rm -rf "$OLD" && say "  Removed ~/.local/lib/JS8FastChat"
    else
        say "  Kept. It is harmless; the menu entry still points here."
    fi
fi
# Other copies of the source install: earlier versions unpacked beside this one
# ("JS8FastChat Ver 1.75.2" next to "JS8FastChat Ver 1.76"), and the first 1.76
# tests, which unpacked into ~/JS8FastChat. Settings are never in these folders.
# Not while FastChat is running: the copy it runs from would vanish under it.
# Only OLDER copies are offered: a newer one beside this is never touched.
tag_of() { grep -o 'APP_BUILD = "[^"]*"' "$1/js8fastchat/constants.py" 2>/dev/null | cut -d'"' -f2; }
MY_TAG="$(tag_of "$APP")"
RUNNING=0
pgrep -f 'JS8FastChat\.py' >/dev/null 2>&1 && RUNNING=1
for d in "$(dirname "$APP")"/"JS8FastChat Ver "* "$HOME"/JS8FastChat/"JS8FastChat Ver "*; do
    [ -f "$d/JS8FastChat.py" ] || continue
    [ "$(cd "$d" && pwd)" = "$APP" ] && continue
    T="$(tag_of "$d")"
    if [ -n "$T" ] && [ "$(printf '%s\n%s\n' "$T" "$MY_TAG" | sort | tail -n 1)" = "$T" ]; then
        say ""; say "  Left alone, it is the same or newer: $d"
        continue
    fi
    say ""
    say "  An older copy is in: $d"
    if [ "$RUNNING" = 1 ]; then
        say "  Kept, because JS8FastChat is running. Close it and run this again to"
        say "  remove it. It is harmless; the menu entry points here instead."
    elif ask "  Remove it? (your settings are kept)"; then
        rm -rf "$d" && say "  Removed."
        rmdir "$HOME/JS8FastChat" 2>/dev/null || true
    else
        say "  Kept. It is harmless; the menu entry points here instead."
    fi
done
case "$APP" in
    "$HOME/Applications/"*) ;;
    *) say ""; say "  Note: installed where it is, $APP"
       say "  (the standard place is ~/Applications)." ;;
esac

# --- 3 + 4. marks and launchers ---------------------------------------------------------
chmod +x "$APP/debug.sh" "$APP/update_linux.sh" "$APP/linux_install.sh" \
         "$APP"/tools/*.sh 2>/dev/null || true
sh "$APP/tools/install_linux_launcher.sh" >/dev/null
say "  Menu entry and desktop icon -- added"

# --- 5. next steps -------------------------------------------------------------------------
say ""
say "DONE. Next:"
say "  1. In JS8Call: File > Settings > Reporting -- tick Enable TCP Server API and"
say "     Accept TCP Requests, port 2442, TCP Max Connections 5. Restart JS8Call."
say "     Count one connection per program: JS8Map, JS8FastChat, and any companion"
say "     such as JS8Spotter or CommStat. At 1 there are not enough to go round,"
say "     and a refused connection is silent."
say "  2. Install JS8Map first if you have not. FastChat reads the callsign database"
say "     JS8Map's first-run wizard downloads, and the cross-app buttons need it."
say "  3. Start order: JS8Call, then JS8Map, then JS8FastChat."
say "  4. A newer version later: close JS8FastChat, save the new .tar.gz in"
say "     Downloads, and paste the same install lines."
