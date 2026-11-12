#!/bin/sh
# install_linux_launcher.sh -- add JS8FastChat to the Linux menu and the desktop.
#
#   sh tools/install_linux_launcher.sh            (run from the app folder)
#   sh tools/install_linux_launcher.sh --remove   (take the entries out)
#
# Writes ~/.local/share/applications/js8fastchat.desktop and a desktop icon.
# StartupWMClass=Js8fastchat is the casing Tk actually reports -- Tk lowercases
# everything after the first character, and the desktop compares the string
# exactly, so "JS8FastChat" here would match nothing. Do not "correct" it.
# This REPLACES the menu entry of an installed 1.75.1, on purpose: same app,
# newer version, so the menu keeps one entry rather than two.
set -e
APP="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
MAIN="$DEST/js8fastchat.desktop"

# Desktop folder (localized names, e.g. "Bureau", come from xdg-user-dir)
DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
DESKICON="$DESK/JS8FastChat.desktop"

if [ "$1" = "--remove" ]; then
    rm -f "$MAIN" "$DESKICON"
    echo "Removed $MAIN and $DESKICON"
    exit 0
fi

ICON="$APP/js8fastchat_icon.png"
if [ -x "$APP/dist/JS8FastChat" ]; then
    EXEC="\"$APP/dist/JS8FastChat\""     # frozen build present: use it
else
    EXEC="sh \"$APP/debug.sh\""          # run from source
fi

mkdir -p "$DEST"
cat > "$MAIN" <<DESKTOP
[Desktop Entry]
Type=Application
Version=1.0
Name=JS8FastChat
GenericName=JS8Call Chat
Comment=Fast per-station chat, messages and SNR for JS8Call
Exec=$EXEC
Path=$APP
Icon=$ICON
Terminal=false
Categories=Network;HamRadio;
StartupNotify=true
StartupWMClass=Js8fastchat
DESKTOP
chmod +x "$MAIN"

# Desktop icon: a copy of the menu entry. Marked trusted so Cinnamon /
# GNOME / MATE start it on double-click without an "untrusted" prompt.
if [ -d "$DESK" ]; then
    cp "$MAIN" "$DESKICON"
    chmod +x "$DESKICON"
    gio set "$DESKICON" metadata::trusted true 2>/dev/null || true
fi
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DEST" >/dev/null 2>&1 || true
echo "Installed:"
echo "  $MAIN"
[ -f "$DESKICON" ] && echo "  $DESKICON  (desktop icon)"
echo "JS8FastChat is now in the menu (Ham Radio / Internet)."
echo "If the taskbar still shows an old icon, log out and back in once."
