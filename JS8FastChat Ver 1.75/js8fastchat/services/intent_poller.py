from __future__ import annotations

"""intent_poller.py — Phase 4 of the JS8FastChat <-> JS8Map integration.

Watches for a JS8Map handoff intent file (js8fastchat_intent.json) and calls
a callback when a fresh, previously-unseen intent is found.

CONTRACT (locked per architecture decisions):
  - FastChat is READ-ONLY with respect to the intent file. It never writes,
    modifies, or deletes it. JS8Map is the sole writer and ages it out itself.
  - An intent is "fresh" if its ISO 8601 UTC timestamp is within
    INTENT_FRESH_SECONDS of now.
  - FastChat tracks the last-acted timestamp string in memory so it never
    opens the popup twice for the same intent write, even across multiple
    polls while the file sits on disk unchanged.
  - The poll cadence is INTENT_POLL_MS (2000 ms), same as the DB-change
    auto-refresh tick, driven by tkinter's after() on the caller's Tk root.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

from ..constants import (
    INTENT_FILENAME,
    INTENT_FRESH_SECONDS,
    INTENT_POLL_MS,
    RAISE_FILENAME,
    SERVER_FILENAME,
    SHARED_DIR,
)
from ..shared_resolver import per_user_shared_dir_path as _shared_per_user_path
from ..utils import debug as _debug


def _appdata_js8map_dirs() -> List[Path]:
    """Mirrors MapDbLocator._appdata_js8map_dirs() -- kept here as a pure
    function so this module has no dependency on the data layer."""
    out: List[Path] = []
    for env in ("LOCALAPPDATA", "APPDATA"):
        raw = os.environ.get(env, "")
        if raw:
            for name in ("JS8Map", "MJsJS8Map", "MJ_JS8Map"):
                p = Path(raw) / name
                if p.exists():
                    out.append(p)
    for p in (
        Path.home() / ".local" / "share" / "JS8Map",
        Path.home() / ".config" / "JS8Map",
    ):
        if p.exists():
            out.append(p)
    return out


def _per_user_shared_dir() -> Optional[Path]:
    """The per-user shared mailbox: %LOCALAPPDATA%\\JS8MapChat\\Shared.

    v1.6.1 (#36). JS8Map's resolver falls through to this folder when it finds
    no sibling shared folder -- which is what happens the moment JS8Map.exe is
    installed to Program Files. FastChat's own constants.py computes the same
    path, but ONLY when ITS OWN walk-up fails. So if JS8Map is installed and
    FastChat still runs from the source tree, JS8Map writes to the per-user
    mailbox while FastChat's SHARED_DIR still points at the sibling folder --
    two different folders, silently, and every button dies. That is ticket #32
    all over again with the folder renamed.

    Fix: look here ALWAYS, regardless of what SHARED_DIR resolved to. Reading a
    folder we don't use costs nothing; not reading it costs the feature.

    NOTE: computed but NEVER CREATED here. FastChat is a READER of the handoff
    files (locked architecture). JS8Map creates this folder when it needs it.

    Phase 7 task 3: the path CALC now lives in shared_resolver's compute-only
    per_user_shared_dir_path() -- the same module the creating resolvers use --
    so the reader's path and the writers' path can no longer drift apart. This
    wrapper keeps the reader contract (Optional[Path], no makedirs, EVER).
    """
    try:
        p = _shared_per_user_path()
        return Path(p) if p else None
    except Exception:
        return None


def _search_dirs(extra_dirs: Optional[List[Path]] = None) -> List[Path]:
    """Every directory that could hold one of JS8Map's three handoff files.

    THE ORDER OF THIS LIST NO LONGER DECIDES ANYTHING. It used to: the finders
    returned the FIRST file they stumbled on, so a stale copy in an
    early-listed folder silently shadowed a live one further down (#34). They
    now pick the NEWEST copy across all of these -- see _newest_existing().
    This list is therefore just "everywhere to look", not a priority ranking.

    1. SHARED_DIR                -- whatever FastChat's own resolver decided
    2. %LOCALAPPDATA%\\JS8MapChat\\Shared -- the per-user mailbox, ALWAYS checked
                                    even when SHARED_DIR is something else (#36)
    3. %LOCALAPPDATA%\\JS8Map etc -- where a FROZEN JS8Map.exe used to write.
                                    Kept so an OLD JS8Map.exe paired with this
                                    FastChat still works. Nothing has to be
                                    upgraded in lockstep.
    4. extra_dirs                -- unchanged (nothing passes any today)
    """
    dirs: List[Path] = []

    def _add(p) -> None:
        if p is None:
            return
        try:
            q = Path(str(p))
            if not q.is_dir():
                return
            r = q.resolve()
            for seen in dirs:
                if seen.resolve() == r:
                    return          # de-duplicate: the same folder can be
            dirs.append(q)          # reached by more than one route
        except Exception:
            pass

    _add(SHARED_DIR)
    _add(_per_user_shared_dir())
    for d in _appdata_js8map_dirs():
        _add(d)
    if extra_dirs:
        for d in extra_dirs:
            _add(d)
    return dirs


def _file_age_key(p: Path) -> float:
    """Rank a handoff file by WHEN IT WAS WRITTEN, as epoch seconds.

    Prefers the file's OWN "timestamp" field over the filesystem mtime: mtime
    can be rewritten by a copy, a backup restore, or a sync tool, whereas the
    timestamp inside the file is what JS8Map actually stamped when it wrote it.
    Falls back to mtime when the field is missing or unparseable, and to 0.0
    (i.e. "oldest possible") if even that fails, so a broken file can never win.
    """
    try:
        data = read_intent(p)
        if data:
            dt = _parse_utc_timestamp(str(data.get("timestamp", "") or ""))
            if dt is not None:
                return dt.timestamp()
    except Exception:
        pass
    try:
        return p.stat().st_mtime
    except Exception:
        return 0.0


_shadow_logged: dict = {}   # filename -> (winner, losers) last logged. See below.


def _newest_existing(filename: str, extra_dirs: Optional[List[Path]] = None) -> Optional[Path]:
    """Return the NEWEST copy of `filename` across every search dir, or None.

    #34. The old code returned the FIRST copy it found, walking the search list
    in order. That made correctness depend on getting the ORDER right, and the
    order was wrong in one direction: a stale file sitting in SHARED_DIR
    silently shadowed a LIVE file written by an older JS8Map.exe into
    %LOCALAPPDATA%\\JS8Map. The button then did nothing, with no error anywhere
    -- the house failure mode (#7).

    Picking the newest removes the whole class of bug. Order stops mattering,
    a leftover file can no longer beat a live one, and no cleanup step has to be
    remembered by a human at 7am.
    """
    hits = []
    for d in _search_dirs(extra_dirs):
        p = d / filename
        try:
            if p.is_file():
                hits.append(p)
        except Exception:
            pass
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    hits.sort(key=_file_age_key, reverse=True)
    winner = hits[0]
    # Log which copies we skipped -- but ONLY when the picture CHANGES.
    # v1.6.1 first cut logged this on EVERY poll tick (2 s), which spammed a
    # debug log that is already 25,000+ lines. Duplicate handoff files are a
    # normal, persistent state; a line every 2 s about it is noise, not signal.
    key = (str(winner), tuple(str(p) for p in hits[1:]))
    if _shadow_logged.get(filename) != key:
        _shadow_logged[filename] = key
        for loser in hits[1:]:
            _debug(f"{filename}: ignoring older copy at {loser} (using {winner})")
    return winner


def find_intent_file(extra_dirs: Optional[List[Path]] = None) -> Optional[Path]:
    """Return the NEWEST existing copy of the intent file, or None."""
    p = _newest_existing(INTENT_FILENAME, extra_dirs)
    if p is None:
        # Log the directories we searched so a real-machine miss is diagnosable
        # from the debug log instead of being invisible.
        dirs = _search_dirs(extra_dirs)
        _debug(f"intent file not found. searched: {[str(d) for d in dirs] or '(no JS8Map dirs resolved)'}")
    return p


def find_raise_file(extra_dirs: Optional[List[Path]] = None) -> Optional[Path]:
    """Return the NEWEST existing copy of the raise-window signal file, or None.

    Mirrors find_intent_file() but stays quiet when absent -- a missing raise
    file is the normal state, not a diagnosable miss."""
    return _newest_existing(RAISE_FILENAME, extra_dirs)


def find_server_file(extra_dirs: Optional[List[Path]] = None) -> Optional[Path]:
    """Return the NEWEST existing copy of JS8Map's server-port file, or None.

    JS8Map publishes its randomly-chosen HTTP port here at startup so FastChat
    can reach /raise_window. A missing file just means JS8Map isn't running (or
    hasn't opened the map yet -- see #27), which is a normal state, so this
    stays quiet like find_raise_file().

    NEWEST matters most here. JS8Map picks a NEW random port every launch, so a
    stale server file does not merely point nowhere -- it points at a port some
    OTHER process may since have taken. Always take the freshest one."""
    return _newest_existing(SERVER_FILENAME, extra_dirs)


def read_server_port(extra_dirs: Optional[List[Path]] = None) -> Optional[int]:
    """Resolve JS8Map's current HTTP port from its server-port file.

    Reads the file FRESH every call (never cached) so a JS8Map restart on a new
    random port is picked up automatically -- the file is rewritten each launch.
    Returns the port as an int, or None if the file is missing, unparseable, or
    the port value is absent/out of range. JS8Map is the sole writer; we only
    read. Uses the same BOM-tolerant reader as the intent/raise files.
    """
    path = find_server_file(extra_dirs)
    if path is None:
        _debug("server-port file not found (JS8Map not running?)")
        return None
    info = read_intent(path)  # same JSON + utf-8-sig reader
    if not info:
        return None
    try:
        port = int(info.get("port"))
    except (TypeError, ValueError):
        _debug(f"server-port file has no valid 'port': {info!r}")
        return None
    if not (1 <= port <= 65535):
        _debug(f"server-port file port out of range: {port}")
        return None
    return port


def read_intent(path: Path) -> Optional[dict]:
    """Read and return the parsed intent dict, or None on any parse/IO error.

    Uses utf-8-sig so a UTF-8 BOM (written by e.g. PowerShell
    [System.IO.File]::WriteAllText, Notepad "Save As UTF-8", etc.) is
    stripped transparently rather than making json.load choke on a leading
    \\ufeff and silently returning None.
    """
    try:
        raw = path.read_text(encoding="utf-8-sig")
        data = json.loads(raw)
        if not isinstance(data, dict):
            _debug(f"intent file parsed but not a dict: {type(data).__name__}")
            return None
        return data
    except Exception as e:
        _debug(f"read_intent failed for {path}: {type(e).__name__}: {e}")
        return None


def _parse_utc_timestamp(ts: str) -> Optional[datetime]:
    """Parse an ISO 8601 UTC timestamp string into an aware datetime, or None.

    Tolerant of: a leading BOM or surrounding whitespace, a trailing 'Z' or
    explicit '+00:00', and naive timestamps (no offset) which are assumed to
    already be UTC. This is deliberately forgiving because the writer side
    (JS8Map, or a test script) may format the timestamp slightly differently
    than expected, and a silent parse failure here would make the whole
    handoff appear broken for no visible reason.
    """
    if not ts:
        return None
    try:
        s = str(ts).strip().lstrip("\ufeff").strip()
        s = s.replace("z", "Z").replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception as e:
        _debug(f"_parse_utc_timestamp failed for {ts!r}: {type(e).__name__}: {e}")
        return None


def is_fresh(intent: dict) -> bool:
    """Return True if the intent's timestamp is within INTENT_FRESH_SECONDS of now."""
    ts = _parse_utc_timestamp(str(intent.get("timestamp", "") or ""))
    if ts is None:
        return False
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    return 0 <= age <= INTENT_FRESH_SECONDS


class IntentPoller:
    """Polls for a fresh JS8Map -> FastChat handoff intent at a fixed cadence.

    Usage:
        poller = IntentPoller(tk_root, on_intent=lambda call: ..., extra_dirs=[...])
        poller.start()   # typically called right after MainWindow is ready
        poller.stop()    # called from on_close() to cancel the pending after()

    The on_intent callback is called on the Tk main thread (it runs from inside
    an after() callback), so it is safe to update UI directly.
    """

    def __init__(
        self,
        root,                              # tk.Tk or any widget with .after()
        on_intent: Callable[[str], None],  # called with the callsign string
        extra_dirs: Optional[List[Path]] = None,
        on_raise: Optional[Callable[[], None]] = None,  # called to raise main window
    ) -> None:
        self._root = root
        self._on_intent = on_intent
        self._on_raise = on_raise
        self._extra_dirs = extra_dirs or []
        self._last_acted_ts: Optional[str] = None
        self._last_raise_ts: Optional[str] = None
        self._after_id = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._schedule()

    def stop(self) -> None:
        self._running = False
        if self._after_id is not None:
            try:
                self._root.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _schedule(self) -> None:
        if self._running:
            self._after_id = self._root.after(INTENT_POLL_MS, self._tick)

    def _tick(self) -> None:
        if not self._running:
            return
        try:
            self._check()
        except Exception:
            pass
        try:
            self._check_raise()
        except Exception:
            pass
        self._schedule()

    def _check_raise(self) -> None:
        """Check for a fresh JS8Map 'raise my window' signal and fire on_raise.

        Same freshness / already-acted dedupe logic as the intent check, but
        carries no callsign -- it just asks FastChat to bring its main window
        to the front. No-op if no on_raise callback was supplied.
        """
        if self._on_raise is None:
            return
        path = find_raise_file(self._extra_dirs or None)
        if path is None:
            return
        signal = read_intent(path)  # same JSON+BOM-tolerant reader
        if not signal:
            return
        ts_raw = str(signal.get("timestamp", "") or "")
        if ts_raw and ts_raw == self._last_raise_ts:
            return
        if not is_fresh(signal):
            return
        self._last_raise_ts = ts_raw
        _debug("raise signal ACTING: bringing FastChat main window to front")
        self._on_raise()

    def _check(self) -> None:
        path = find_intent_file(self._extra_dirs or None)
        if path is None:
            return
        intent = read_intent(path)
        if not intent:
            _debug(f"intent file found at {path} but did not parse to a dict")
            return
        callsign = str(intent.get("callsign", "") or "").strip().upper()
        if not callsign:
            _debug(f"intent at {path} has no callsign: {intent!r}")
            return
        ts_raw = str(intent.get("timestamp", "") or "")
        fresh = is_fresh(intent)
        _debug(f"intent seen: call={callsign} ts={ts_raw!r} fresh={fresh} last_acted={self._last_acted_ts!r}")
        # Skip if we already acted on this exact timestamp.
        if ts_raw and ts_raw == self._last_acted_ts:
            return
        if not fresh:
            return
        # Fresh, unseen intent — act on it.
        self._last_acted_ts = ts_raw
        _debug(f"intent ACTING: opening FastChat popup for {callsign}")
        self._on_intent(callsign)
