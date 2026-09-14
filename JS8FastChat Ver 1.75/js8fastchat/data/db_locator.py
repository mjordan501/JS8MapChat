from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Iterable, List

from ..constants import (
    CANADIAN_DB_CANDIDATES,
    CANADIAN_DB_FILENAMES,
    DEFAULT_GROUPS,
    FCC_DB_CANDIDATES,
    GROUP_ALL,
    ROOT_DIR,
)
from ..models import UserAppConfig
from ..utils import base_call, first_existing, norm_call, read_json
from .js8call_ini import read_js8call_groups, read_js8call_highlights

# How far up from ROOT_DIR to look for a sibling JS8Map source tree, and the
# file that identifies one. Matched by CONTENT, never by folder name: the name
# carries a version ("JS8Map Ver 1.75") and version-baked name matching is the
# exact fragility _SHARED_DIR_NAME was changed to remove.
_SIBLING_WALK_UP_LEVELS = 5
_MAP_TREE_MARKER = "ham_map_config.json"


class MapDbLocator:
    """Find JS8Map config and DB files without touching the DB on UI startup."""

    def __init__(self, app_config: UserAppConfig):
        self.app_config = app_config
        self.map_config_path = ""
        self.map_config: dict = {}
        self.spot_db = ""
        self.relay_db = ""
        self.fcc_db = ""
        self.canadian_db = ""
        self._sibling_dirs_cache: list[Path] | None = None

    def refresh(self) -> None:
        self.map_config_path = self._find_map_config()
        self.map_config = read_json(Path(self.map_config_path)) if self.map_config_path else {}
        self.spot_db = self._find_spot_db()
        self.relay_db = self._find_relay_db()
        self.fcc_db = self._find_fcc_db()
        self.canadian_db = self._find_canadian_db()

    def _add_dir(self, dirs: list[Path], raw: str | Path | None) -> None:
        if not raw:
            return
        try:
            p = Path(raw).expanduser()
            if p.is_file():
                p = p.parent
            p = p.resolve()
            if p not in dirs:
                dirs.append(p)
        except Exception:
            pass

    def _appdata_js8map_dirs(self) -> list[Path]:
        out: list[Path] = []
        for env in ("LOCALAPPDATA", "APPDATA"):
            raw = os.environ.get(env, "")
            if raw:
                for name in ("JS8Map", "MJsJS8Map", "MJ_JS8Map"):
                    p = Path(raw) / name
                    if p.exists():
                        out.append(p)
        # Non-Windows fallbacks for later Linux/Mac testing.
        for p in (Path.home() / ".local" / "share" / "JS8Map", Path.home() / ".config" / "JS8Map"):
            if p.exists():
                out.append(p)
        return out

    def _sibling_js8map_dirs(self) -> list[Path]:
        """Folders that LOOK like a JS8Map tree, found by walking up from ROOT_DIR
        and checking each ancestor's direct children for ham_map_config.json.

        WHY THIS EXISTS: in SOURCE (unfrozen) mode JS8Map writes its databases
        BESIDE ITS OWN CODE, and nothing else in _candidate_dirs() ever pointed
        there -- ROOT_DIR is FASTCHAT'S folder, and the two apps sit as SIBLINGS,
        not parent/child. On Linux that left FastChat resolving to a stale
        per-user DB that nobody was writing to, showing an empty activity table
        while the header read "connected". FINDINGS #4 reproducing off-Windows.

        Identified by CONTENT, not by name -- see _MAP_TREE_MARKER above.
        Bounded walk, results cached per instance (refresh() re-enters the
        candidate list several times and this touches the filesystem).
        """
        if self._sibling_dirs_cache is not None:
            return self._sibling_dirs_cache
        out: list[Path] = []
        try:
            cur = Path(ROOT_DIR).resolve()
            for _ in range(_SIBLING_WALK_UP_LEVELS):
                try:
                    for child in sorted(cur.iterdir()):
                        if child.is_dir() and (child / _MAP_TREE_MARKER).is_file():
                            if child not in out:
                                out.append(child)
                except Exception:
                    pass
                parent = cur.parent
                if parent == cur:
                    break
                cur = parent
        except Exception:
            pass
        self._sibling_dirs_cache = out
        return out

    def _candidate_dirs(self) -> List[Path]:
        dirs: list[Path] = []
        for raw in (ROOT_DIR, Path.cwd(), self.app_config.map_config_path, self.app_config.spot_db_path, self.app_config.relay_db_path):
            self._add_dir(dirs, raw)
        for p in self._appdata_js8map_dirs():
            self._add_dir(dirs, p)
        # APPENDED, never inserted: a sibling source tree is a LAST-RESORT anchor.
        # _prefer_live_db()'s ordering is load-bearing (see its docstring) and
        # nothing above this line moves.
        for p in self._sibling_js8map_dirs():
            self._add_dir(dirs, p)
        out_path = str(self.map_config.get("out_path", "") or "")
        self._add_dir(dirs, out_path)
        return dirs

    def _find_map_config(self) -> str:
        candidates: list[Path | str] = []
        if self.app_config.map_config_path:
            candidates.append(self.app_config.map_config_path)
        for folder in [ROOT_DIR, Path.cwd()] + self._appdata_js8map_dirs() + self._sibling_js8map_dirs():
            candidates.append(Path(folder) / "ham_map_config.json")
        return first_existing(candidates)

    def _prefer_live_db(self, names: Iterable[str], saved: str = "") -> str:
        """Resolve a live DB path. AppData FIRST, then the saved path.

        ORDER MATTERS AND USED TO BE WRONG. `saved` was appended BEFORE the
        AppData loop, so a stored path won unconditionally -- the exact
        opposite of the "prefer AppData" comment that sat two lines below it.

        That made resolution LATCH. main_window.save_current_config() (line
        ~6237) writes locator.spot_db back into the config on every save, so
        whatever resolved once got stored and then won forever. Concretely:
        launch FastChat before JS8Map has ever created %LOCALAPPDATA%\\JS8Map\\,
        _appdata_js8map_dirs() returns empty, resolution falls through to
        ROOT_DIR/cwd and finds the repo's STALE js8_spots.db, that path is
        saved, and it keeps winning even after AppData appears. Silently: no
        error, no log line, and every symptom looks like a data bug rather
        than a path bug. "OPEN THE MAP FIRST, ALWAYS" is what has been
        preventing this, not luck.

        AppData now goes first, which is what the comment always claimed.
        `saved` still beats the generic candidate dirs (repo root, cwd), so a
        deliberately chosen custom location OUTSIDE AppData is still honored --
        but a live AppData copy overrides a stored path that points elsewhere.
        """
        candidates: list[Path | str] = []
        # LIVE AppData copies outrank any stored path. See docstring.
        for folder in self._appdata_js8map_dirs():
            for name in names:
                candidates.append(folder / name)
        if saved:
            candidates.append(saved)
        for folder in self._candidate_dirs():
            for name in names:
                candidates.append(folder / name)
        return first_existing(candidates)

    def _find_spot_db(self) -> str:
        return self._prefer_live_db(["js8_spots.db", "js8_spots_TEST.db"], self.app_config.spot_db_path)

    def _find_relay_db(self) -> str:
        return self._prefer_live_db(["js8_relay.db", "js8_relay_TEST.db"], self.app_config.relay_db_path)

    def _find_fcc_db(self) -> str:
        candidates: list[Path | str] = []
        for raw in (self.app_config.fcc_db_path, str(self.map_config.get("fcc_db_path", "") or "")):
            if raw:
                p = Path(raw)
                if p.is_dir():
                    candidates.append(p / "ham.db")
                candidates.append(p)
        for folder in self._candidate_dirs():
            candidates.append(folder / "ham.db")
        candidates.extend(FCC_DB_CANDIDATES)
        return first_existing(candidates)

    def _find_canadian_db(self) -> str:
        candidates: list[Path | str] = []
        keys = ("canadian_db_path", "canada_db_path", "canadian_callsign_db_path", "canadian_callsigns_db_path", "canadian_db", "canada_db")
        raws = [self.app_config.canadian_db_path] + [str(self.map_config.get(k, "") or "") for k in keys]
        for raw in raws:
            if not raw:
                continue
            p = Path(raw)
            if p.is_dir():
                for filename in CANADIAN_DB_FILENAMES:
                    candidates.append(p / filename)
            candidates.append(p)
        for folder in self._candidate_dirs():
            for filename in CANADIAN_DB_FILENAMES:
                candidates.append(folder / filename)
        for raw in CANADIAN_DB_CANDIDATES:
            p = Path(raw)
            for filename in CANADIAN_DB_FILENAMES:
                candidates.append(p / filename)
            candidates.append(p)
        return first_existing(candidates)

    def host(self) -> str:
        return str(self.map_config.get("js8_host", self.app_config.host) or self.app_config.host)

    def port(self) -> int:
        try:
            return int(self.map_config.get("js8_port", self.app_config.port) or self.app_config.port)
        except Exception:
            return self.app_config.port

    def callsign(self) -> str:
        return norm_call(self.map_config.get("callsign", ""))

    def grid(self) -> str:
        return str(self.map_config.get("my_grid", "") or "").upper().strip()

    def _groups_from_spot_db(self) -> set[str]:
        groups: set[str] = set()
        if not self.spot_db or not Path(self.spot_db).exists():
            return groups
        try:
            # closing(), not the bare form: `with sqlite3.connect(...)` commits
            # the TRANSACTION but never closes the CONNECTION, so these handles
            # leaked for the life of the process. Read-only URI, nothing to lose.
            with closing(sqlite3.connect(f"file:{self.spot_db}?mode=ro", uri=True, timeout=2)) as con:
                for sql in (
                    "SELECT DISTINCT UPPER(group_name) FROM group_activity WHERE group_name LIKE '@%' ORDER BY group_name",
                    "SELECT DISTINCT UPPER(to_call) FROM directed WHERE to_call LIKE '@%' ORDER BY to_call",
                ):
                    try:
                        for (g,) in con.execute(sql):
                            if g and g != "@HB":
                                groups.add(str(g).upper())
                    except Exception:
                        pass
        except Exception:
            pass
        return groups

    def groups(self, include_db_groups: bool = False) -> List[str]:
        groups = set(read_js8call_groups(self.app_config.js8call_ini_path))
        if include_db_groups:
            groups.update(self._groups_from_spot_db())
        raw = str(self.map_config.get("my_groups", "") or "")
        for token in raw.replace(";", ",").split(","):
            g = token.strip().upper()
            if not g:
                continue
            if not g.startswith("@"):
                g = "@" + g
            groups.add(g)
        if not groups:
            groups.update(g for g in DEFAULT_GROUPS if str(g).startswith("@"))
        groups.add("@ALLCALL")
        return [GROUP_ALL] + sorted(g for g in groups if g != GROUP_ALL)

    def watched_calls(self) -> set[str]:
        watched: set[str] = set()
        for item in self.map_config.get("watched_calls", []) or []:
            b = base_call(item)
            if b:
                watched.add(b)
        if self.spot_db and Path(self.spot_db).exists():
            try:
                with closing(sqlite3.connect(f"file:{self.spot_db}?mode=ro", uri=True, timeout=2)) as con:
                    for (call,) in con.execute("SELECT callsign FROM watched_calls"):
                        b = base_call(call)
                        if b:
                            watched.add(b)
            except Exception:
                pass
        watched.update(read_js8call_highlights(self.app_config.js8call_ini_path))
        return watched

    def tx_halt_enabled(self) -> bool:
        return bool(self.map_config.get("tx_halt_enabled", False))

    def summary_lines(self) -> list[str]:
        return [
            f"Map config: {self.map_config_path or '(not found)'}",
            f"JS8 host/port: {self.host()}:{self.port()}",
            f"Station: {self.callsign() or '(unknown)'} {self.grid() or ''}",
            f"Spot DB: {self.spot_db or '(not found)'}",
            f"Relay DB: {self.relay_db or '(not found)'}",
            f"FCC DB: {self.fcc_db or '(not found)'}",
            f"Canadian DB: {self.canadian_db or '(not found)'}",
            f"TX Halt enabled in JS8Map config: {self.tx_halt_enabled()}",
        ]
