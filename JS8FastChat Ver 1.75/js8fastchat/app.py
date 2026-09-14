from __future__ import annotations

import sys

from .ui.main_window import MainWindow
from .fastchat_heartbeat import FastChatHeartbeat


def main() -> None:
    """Entry point.

    Normal launch (no arguments): the full console, as before.

    Phase 3 standalone launch: `--callsign CALLSIGN` opens a MainWindow with
    the console hidden and the FastChat popup pre-targeted to CALLSIGN.
    This is what Phase 4's JS8Map handoff (button -> intent file -> FastChat
    polls and opens pre-targeted) will call into; for now it is reachable
    directly from the command line for testing. See
    MainWindow.launch_standalone_popup for what "hidden" actually means
    (the real console object, just withdrawn -- not a lite/stripped popup).
    """
    # Windows: claim a distinct AppUserModelID BEFORE MainWindow() is built.
    # MainWindow subclasses tk.Tk, so constructing it creates the Tk root; the
    # ID must be set first. Without it Windows keys the taskbar to python.exe --
    # giving the generic Python icon on the running window and grouping FastChat
    # into the same taskbar button as JS8Map (the .exe file icon, set via the
    # spec, is already correct; this is purely the running-window identity).
    # A distinct ID from JS8Map's makes the two apps separate taskbar buttons.
    # Guarded so a failure can never stop launch; no-op off Windows.
    try:
        if sys.platform.startswith("win"):
            import ctypes as _appid_ctypes
            _appid_ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "KW3KW.JS8MapChat.JS8FastChat.1.75"
            )
    except Exception:
        pass

    callsign = ""
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg in ("--callsign", "-c") and i + 1 < len(args):
            callsign = args[i + 1]
            break
        if arg.startswith("--callsign="):
            callsign = arg.split("=", 1)[1]
            break

    app = MainWindow()
    if callsign.strip():
        app.launch_standalone_popup(callsign)

    # Phase 10: presence heartbeat. Writes js8fastchat_alive.json into the
    # shared folder so JS8Map can detect FastChat is running and enable the
    # FastChat button in its callsign popup. Refreshes on a daemon thread and
    # deletes the sentinel on exit (atexit also covers a hard close). Guarded
    # so a heartbeat failure can never stop FastChat from launching.
    heartbeat = None
    try:
        heartbeat = FastChatHeartbeat()
        heartbeat.start()
    except Exception as exc:  # noqa: BLE001
        print(f"[app] WARNING: could not start FastChat heartbeat: {exc}")

    try:
        app.mainloop()
    finally:
        if heartbeat is not None:
            heartbeat.stop()
