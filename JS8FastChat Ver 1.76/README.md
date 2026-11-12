# JS8FastChat

A per-station chat console for [JS8Call](http://js8call.com). It shows who is on
the air, gives each station its own chat window with history, signal reports,
stored messages and an inbox, and puts JS8Call's right-click commands on single
buttons.

JS8FastChat is one half of **[JS8MapChat](https://github.com/mjordan501/JS8MapChat)**.
JS8Map Spotter draws the map; FastChat does the talking. Each has a button that
brings the other to the front. This folder is FastChat; the map is beside it.

Current version: **1.76** (build 2026-10-31). Windows and Linux.

---

## Before you install

1. **Install JS8Map Spotter first** and let its first-run setup download the
   callsign databases. FastChat reads them.
2. **In JS8Call:** File > Settings > Reporting. Tick *Enable TCP Server API*
   and *Accept TCP Requests*, port **2442**, *TCP Max Connections* **5**.
   Restart JS8Call.
   Each program that talks to JS8Call (JS8Map, FastChat, JS8Spotter, CommStat)
   takes one connection. At the default of 1, the programs that miss out fail
   silently: they still show activity, but the frequency and offset boxes stay
   empty and nothing transmits.

Start order: **JS8Call, then JS8Map, then JS8FastChat.**

---

## Install on Windows

1. Open the [Releases](https://github.com/mjordan501/JS8MapChat/releases/latest) page and download
   `JS8FastChat_v1.76_Setup.exe`. Your browser saves it to your Downloads folder.
2. Double-click it in Downloads and follow the installer.
3. Start JS8FastChat from the desktop icon.

To update, download the newer `Setup.exe` and run it. Your settings are kept.

## Install on Linux

Needs Python 3.8 or newer with Tk. The installer checks, and offers to add
anything missing (`python3-tk`, `unzip`, `wmctrl`, `xdotool`).

1. Open the [Releases](https://github.com/mjordan501/JS8MapChat/releases/latest) page and download
   `JS8FastChat_Ver_1.76_build_2026-10-31_Linux.tar.gz`. Your browser saves it to
   your Downloads folder. Do not unpack it yourself.
2. Open a terminal (Ctrl+Alt+T), paste these three lines at once, and press Enter:

```sh
F="$(ls -t ~/Downloads/JS8FastChat_Ver_*_Linux.tar.gz | head -n 1)"
mkdir -p ~/Applications && tar -xzf "$F" -C ~/Applications
sh ~/Applications/"$(tar -tzf "$F" | head -n 1 | cut -d/ -f1)"/linux_install.sh
```

It unpacks the newest download into `~/Applications`, checks for Python and Tk,
and adds the menu entry and desktop icon. Its questions end in `[y/N]`: type
**y** and press Enter for yes. Enter alone means no. It does not start the
program; use the new JS8FastChat icon.

**To update:** close JS8FastChat, download the newer `_Linux.tar.gz` into
Downloads, and paste the same three lines. The installer offers to remove the
older copy and moves the menu entry to the new one.

**To remove:** close JS8FastChat and paste these. Each line works on its own.
Your settings in `~/.local/share/JS8MapChat/FastChat` are kept; delete that
folder too if you want them gone, but leave `Shared` beside it, which JS8Map uses.

```sh
rm -rf ~/Applications/"JS8FastChat Ver "*
rm -f ~/.local/share/applications/js8fastchat.desktop
rm -f ~/Desktop/JS8FastChat.desktop
```

**If the desktop icon says Untrusted:** right-click it and choose *Allow
Launching* (Linux Mint: *Mark as Trusted*).

---

## Where your settings live

Never in the program folder, so installing, updating, moving or removing the
program does not touch them.

| | Windows | Linux |
|---|---|---|
| Settings and logs | `%LOCALAPPDATA%\JS8MapChat\FastChat` | `~/.local/share/JS8MapChat/FastChat` |
| JS8Map handoff | `%LOCALAPPDATA%\JS8MapChat\Shared` | `~/.local/share/JS8MapChat/Shared` |

---

## Documentation

- **[Installing JS8FastChat on Linux](../docs/Installing_JS8FastChat_on_Linux.pdf)** -
  the install below, illustrated, with troubleshooting.
- **[JS8FastChat Instructional Guide](../docs/JS8FastChat_Instructional_Guide_Ver1_76.pdf)** -
  what every control does.
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - how the program is built, for
  contributors.

## For contributors

Run from source: `sh debug.sh` (Linux) or `Run_JS8FastChat_Debug.bat` (Windows).

Release packages are made only by `tools/make_release.py`, never by hand. It
checks the version strings, checks that `shared_resolver.py` still matches
JS8Map's copy (as `check_twins.bat` at the repository root does for the
Windows build), and refuses to ship a package containing operator data. See
ARCHITECTURE.md section 8.

License: GPL-3.0, see [LICENSE](../LICENSE) at the repository root.
Problems: the [Issues](https://github.com/mjordan501/JS8MapChat/issues) tab.

73 de KW3KW - Ham Made Simple
