"""
fastchat_heartbeat.py  --  JS8FastChat presence sentinel for JS8Map

Writes and refreshes  js8fastchat_alive.json  in SHARED_DIR so JS8Map
knows FastChat is running and can enable the FastChat button in its
callsign popup.

CONTRACT
--------
* FastChat is the SOLE WRITER of this file.
* JS8Map ONLY READS it -- never writes, never deletes it.
* On a clean FastChat exit the file is deleted.
* On a crash / hard-kill the file goes stale; JS8Map considers it dead
  after ALIVE_STALE_SECONDS (see below).

FILE FORMAT  (js8fastchat_alive.json)
--------------------------------------
{
    "pid":     12345,
    "version": "1.75",
    "ts":      1720000000.0      <- float UTC epoch, refreshed every ALIVE_REFRESH_SECONDS
}

USAGE
-----
At FastChat startup (e.g. in JS8FastChat.py, just before root.mainloop()):

    from fastchat_heartbeat import FastChatHeartbeat
    _heartbeat = FastChatHeartbeat()
    _heartbeat.start()

That's it.  atexit handles cleanup automatically; no explicit stop() call needed
unless you want to be tidy in on_closing().

    _heartbeat.stop()   # optional -- atexit fires this anyway
"""

from __future__ import annotations

import atexit
import json
import os
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Tuning constants -- JS8Map must use the same ALIVE_STALE_SECONDS value
# ---------------------------------------------------------------------------
ALIVE_REFRESH_SECONDS = 5    # how often FastChat rewrites the timestamp
ALIVE_STALE_SECONDS   = 15   # JS8Map considers FastChat dead after this gap
                              # (must be > ALIVE_REFRESH_SECONDS; 3x is safe)
ALIVE_FILENAME = "js8fastchat_alive.json"


class FastChatHeartbeat:
    """
    Daemon thread that keeps js8fastchat_alive.json fresh while FastChat runs.
    Deletes the file on clean exit via atexit (also callable as .stop()).
    """

    def __init__(self, shared_dir: Path | None = None):
        # If no path is supplied, import SHARED_DIR from constants.
        # Try package-relative first (js8fastchat.constants), then a plain
        # top-level import, then fall back to writing beside this file.
        if shared_dir is None:
            _shared = None
            _ver    = "unknown"
            try:
                from .constants import SHARED_DIR as _shared, APP_VERSION as _ver  # type: ignore
            except Exception:
                try:
                    from constants import SHARED_DIR as _shared, APP_VERSION as _ver  # type: ignore
                except Exception:
                    _shared = None
            if _shared is not None:
                self._path    = Path(_shared) / ALIVE_FILENAME
                self._version = _ver
            else:
                # Fallback: write beside this file (useful for isolated tests)
                self._path    = Path(__file__).resolve().parent / ALIVE_FILENAME
                self._version = "unknown"
        else:
            self._path    = Path(shared_dir) / ALIVE_FILENAME
            self._version = "unknown"

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Write the file immediately, then start the refresh thread."""
        self._write()
        self._thread = threading.Thread(
            target=self._run,
            name="FastChatHeartbeat",
            daemon=True,           # won't block interpreter shutdown
        )
        self._thread.start()
        atexit.register(self.stop)

    def stop(self) -> None:
        """Signal the thread to exit and delete the sentinel file."""
        self._stop_event.set()
        self._delete()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop_event.wait(timeout=ALIVE_REFRESH_SECONDS):
            self._write()

    def _write(self) -> None:
        payload = {
            "pid":     os.getpid(),
            "version": self._version,
            "ts":      time.time(),
        }
        try:
            # Write to a temp file then rename -- atomic on Windows NTFS
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception as exc:  # noqa: BLE001
            print(f"[FastChatHeartbeat] WARNING: could not write {self._path}: {exc}")

    def _delete(self) -> None:
        try:
            self._path.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[FastChatHeartbeat] WARNING: could not delete {self._path}: {exc}")
