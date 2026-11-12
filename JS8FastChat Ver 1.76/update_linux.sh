#!/bin/sh
# update_linux.sh -- update JS8FastChat on Linux with one command.
#
#   sh ~/Applications/JS8FastChat*/update_linux.sh                 newest package in ~/Downloads
#   sh ~/Applications/JS8FastChat*/update_linux.sh FILE.tar.gz     a particular package
#
# A package is the _Linux.tar.gz (the normal one, from 1.76 on) or a .zip
# (older builds, or the both-systems backup zip). Both work.
#
# What it does:
#   1. picks the package: the one you name, or the NEWEST
#      JS8FastChat_Ver_*_build_*.tar.gz or .zip in ~/Downloads (a "_Windows"
#      zip is never picked); shows the current and the new build;
#   2. refuses a package that carries operator data (settings, the sent-message
#      log or a callsign database);
#   3. stops if JS8FastChat is running from this folder (close it first);
#   4. if the version changed, renames this folder to the zip's folder name
#      ("JS8FastChat Ver 1.75.2" -> "JS8FastChat Ver 1.75.3"), so your settings
#      move with it;
#   5. unpacks over the folder (your settings are not in it, so they are
#      kept), restores the "executable" marks a Windows PC may have dropped,
#      and re-points the menu entry and desktop icon.
#
# Always start it with "sh", as above: that works even when the file is not
# marked executable. Run it from anywhere.
set -e

say()  { printf '%s\n' "$*"; }
stop() { printf '\nSTOP: %s\nNothing was changed.\n' "$*" >&2; exit 1; }
ask()  {   # ask "question" -> 0 for yes. No terminal -> no.
    printf '%s [y/N] ' "$1"
    a=''; { read -r a < /dev/tty; } 2>/dev/null || a=''
    case "$a" in [Yy]|[Yy][Ee][Ss]) return 0 ;; *) return 1 ;; esac
}

SELF="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$SELF")"
NAME="$(basename "$SELF")"
[ -f "$SELF/JS8FastChat.py" ] || stop "run this from the JS8FastChat folder's own copy, e.g.
      sh ~/Applications/JS8FastChat*/update_linux.sh"
# --- 1. which package --------------------------------------------------------------
ZIP=''
for a in "$@"; do
    case "$a" in *.zip|*.tar.gz) ZIP="$a" ;; *) say "(ignoring '$a')" ;; esac
done
if [ -z "$ZIP" ]; then
    DL="$(xdg-user-dir DOWNLOAD 2>/dev/null || echo "$HOME/Downloads")"
    # newest first; package names never contain newlines, so ls | grep is safe here
    # shellcheck disable=SC2010
    ZIP="$(ls -t "$DL"/JS8FastChat_Ver_*_build_*.tar.gz "$DL"/JS8FastChat_Ver_*_build_*.zip 2>/dev/null | grep -v '_Windows' | head -n 1 || true)"
    [ -n "$ZIP" ] || stop "no JS8FastChat_Ver_..._build_..._Linux.tar.gz found in $DL
      Save the new package there, or name it:  sh $0 /path/to/the.tar.gz"
fi
[ -f "$ZIP" ] || stop "file not found: $ZIP"

# The same three jobs for either kind of package: list, read one file, unpack.
case "$ZIP" in
    *.tar.gz)
        pkg_list() { tar -tzf "$ZIP" | sed 's#^\./##'; }
        pkg_cat()  { tar -xzOf "$ZIP" "$1"; }
        pkg_into() { tar -xzf "$ZIP" -C "$1"; } ;;
    *)
        command -v unzip >/dev/null 2>&1 || stop "the 'unzip' program is missing:  sudo apt install unzip"
        pkg_list() { unzip -Z1 "$ZIP"; }
        pkg_cat()  { unzip -p "$ZIP" "$1"; }
        pkg_into() { unzip -o -q "$ZIP" -d "$1"; } ;;
esac

NEW="$(pkg_list 2>/dev/null | head -n 1 | cut -d/ -f1)"
case "$NEW" in
    "JS8FastChat Ver "*) ;;
    *) stop "$ZIP is not a JS8FastChat package (its folder is '$NEW')." ;;
esac
if pkg_list | grep -Eq '(^|/)(mjs_js8fastchat_[^/]*\.json|mjs_js8maps_[^/]*\.json|js8fastchat_(layout|tx_history|capture)_[^/]*\.json|js8fastchat_(intent|alive)\.json)$|\.db(-wal|-shm)?$|\.log$'; then
    stop "$ZIP contains operator data (settings, the sent-message log or a database).
      Do not install it. Use a newer build."
fi

tag_of() { grep -o 'APP_BUILD = "[^"]*"' 2>/dev/null | cut -d'"' -f2; }
OLD_TAG="$(tag_of < "$SELF/js8fastchat/constants.py" || true)"
NEW_TAG="$(pkg_cat "$NEW/js8fastchat/constants.py" 2>/dev/null | tag_of || true)"
[ -n "$NEW_TAG" ] || stop "could not read the build tag inside $ZIP (incomplete download?)"

say "JS8FastChat update"
say "  package:  $ZIP"
say "  folder:   $SELF"
say "  build:    ${OLD_TAG:-?}  ->  $NEW_TAG"
[ "$NAME" = "$NEW" ] || say "  rename:   '$NAME'  ->  '$NEW'"
if [ "$NEW_TAG" = "$OLD_TAG" ]; then
    say "  (same build -- the files will be refreshed)"
elif [ -n "$OLD_TAG" ] && [ "$(printf '%s\n%s\n' "$OLD_TAG" "$NEW_TAG" | sort | tail -n 1)" = "$OLD_TAG" ]; then
    ask "  The package is OLDER than what is installed. Go back to it?" || { say "Nothing was changed."; exit 0; }
fi

# --- 3. not while it is running from this folder ----------------------------------
# A match counts only if the command line really runs JS8FastChat.py AND its
# working folder is this one. Re-read /proc rather than trusting pgrep's own
# match, and never count this script or its parent.
for pid in $(pgrep -f 'JS8FastChat\.py' 2>/dev/null || true); do
    [ "$pid" = "$$" ] && continue
    [ "$pid" = "${PPID:-0}" ] && continue
    [ "$(readlink "/proc/$pid/cwd" 2>/dev/null)" = "$SELF" ] || continue
    tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q 'JS8FastChat\.py' || continue
    stop "JS8FastChat is running from this folder (process $pid).
      Close it and its chat windows, then run this again."
done

# --- 4. rename on a version change ----------------------------------------------
cd "$ROOT"
DEST="$ROOT/$NEW"
if [ "$NAME" != "$NEW" ]; then
    [ -e "$DEST" ] && stop "both '$NAME' and '$NEW' exist in $ROOT.
      Keep the one with your settings (it has mjs_js8fastchat_3_2_config.json),
      rename or remove the other, and run this again."
    mv "$SELF" "$DEST"
    say "Renamed the folder to '$NEW' (your settings moved with it)."
fi

# --- 5. unpack over it, fix the marks, re-point the launchers ----------------------
pkg_into "$ROOT"
chmod +x "$DEST/debug.sh" "$DEST/update_linux.sh" "$DEST/linux_install.sh" \
         "$DEST"/tools/*.sh 2>/dev/null || true
sh "$DEST/tools/install_linux_launcher.sh" >/dev/null
GOT="$(tag_of < "$DEST/js8fastchat/constants.py" || true)"

say ""
say "DONE. JS8FastChat is now build $GOT in:"
say "  $DEST"
say "The menu entry and desktop icon point there. Start it from the icon;"
say "a chat window's title bar shows the version."
