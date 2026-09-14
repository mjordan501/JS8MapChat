from __future__ import annotations

import os as _os
import sys as _sys
from pathlib import Path

APP_VERSION = "1.75"
APP_BUILD = "2026-07-26-JS8MapChat-Ver-1.75"
APP_NAME = "JS8MapChat"
APP_TITLE = f"{APP_NAME} {APP_VERSION} — JS8Map + FastChat Integration"

# ==============================================================================
# TICKET #26 FIX (2026-07-13) -- READ THIS BEFORE CHANGING ANY PATH BELOW.
# ==============================================================================
# THE BUG: ROOT_DIR used to be  Path(__file__).resolve().parents[1]  -- raw
# __file__, with no frozen check. Six WRITABLE files hung off it (config, legacy
# config, layout, debug log, TX history, capture). Under PyInstaller __file__
# points into the per-launch _MEIPASS temp extraction, which Windows DELETES ON
# EXIT. A frozen FastChat would therefore have written its config and window
# layout into a temp folder and lost them on every close -- silently, in a
# process Launch_JS8FastChat_*.vbs runs HIDDEN (shell.Run cmd, 0, False), so
# with no window and no error to see.
#
# _anchor_dir() below already solved this correctly for SHARED_DIR. ROOT_DIR
# never got the same treatment. That was the whole of ticket #26.
#
# THE FIX -- ONE NAME, ONE JOB:
#   _SOURCE_DIR  the literal on-disk location of this source tree. Under
#                PyInstaller this is _MEIPASS. NEVER write here. Bundled,
#                read-only assets only.
#   ROOT_DIR     the app's REAL folder on disk (dir of the .exe when frozen,
#                the app root when running as a script). Used as a SEARCH
#                ANCHOR -- the shared-folder walk-up, and db_locator's hunt for
#                JS8Map's databases. Safe to read. Do not assume it is writable:
#                installed to C:\Program Files\ it is read-only to a normal user.
#   DATA_DIR     FastChat's OWN per-user writable data. Config, layout, debug
#                log, TX history, capture. This is the ONLY place this app
#                writes its own state.
#   SHARED_DIR   the JS8Map <-> FastChat mailbox. UNCHANGED. Do not touch.
#
# WHY %LOCALAPPDATA% AND NOT "beside the .exe": DATA_DIR must not depend on how
# the app was launched. Script, frozen exe, Documents tree, Program Files, a
# stray copy on another drive -- every one of them now computes the SAME folder.
# This is the same fixed-point contract _per_user_shared_dir() already uses for
# the mailbox (H3), and it is what makes an installer possible at all.
#
# Every write path in this app is now install-location-independent.
# ==============================================================================

# The literal on-disk location of this source tree. _MEIPASS when frozen.
# READ-ONLY ASSETS ONLY. Never write here, never anchor user data to it.
_SOURCE_DIR = Path(__file__).resolve().parents[1]

# v1.6: THE VERSION IS OUT OF THE FOLDER NAME.
# In v1.5 the two apps agreed on the shared mailbox by string-matching a folder
# name with the version baked in. That made a version bump a breaking change to
# the contract between two separately edited programs. The canonical name is now
# version-free; the old name is still ACCEPTED so an existing install keeps
# working with no renaming.
# MUST stay identical to JS8Map.py::_FC_SHARED_DIR_NAMES.
_SHARED_DIR_NAME = "JS8MapChat Shared"                  # canonical
_SHARED_DIR_NAMES = ("JS8MapChat Shared",
                     "JS8MapChat Shared Ver 1.5")       # accepted, priority order
_WALK_UP_MAX_LEVELS = 5   # must match JS8Map's _FC_WALK_UP_MAX_LEVELS


def _anchor_dir() -> Path:
    """The stable on-disk folder this process lives in.

    Under PyInstaller, __file__ points into the per-launch _MEIPASS temp
    extraction, so a __file__-derived path would be a temp folder you cannot
    walk up from to find anything. Mirrors JS8Map's _EXE_DIR/_APP_DIR split.
    """
    if getattr(_sys, "frozen", False):
        return Path(_sys.executable).resolve().parent
    return _SOURCE_DIR


# ROOT_DIR = the app's REAL folder on disk, frozen or not. Search anchor.
# Callers that import ROOT_DIR (db_locator.py) are looking for a place to LOOK,
# not a place to WRITE -- which is exactly what this now gives them, and what
# raw __file__ silently stopped giving them the moment the app was frozen.
ROOT_DIR = _anchor_dir()


# ------------------------------------------------------------------------------
# DATA_DIR -- FastChat's own per-user writable data. (Ticket #26)
# ------------------------------------------------------------------------------

def _resolve_data_dir() -> Path:
    """FastChat's per-user data folder, CREATED if absent.

    Deliberately the same shape as _per_user_shared_dir() one folder over:
        %LOCALAPPDATA%\\JS8MapChat\\Shared     <- the two-app mailbox (H3)
        %LOCALAPPDATA%\\JS8MapChat\\FastChat   <- THIS: FastChat's own state

    Independent of install location, so it is identical whether FastChat runs
    from the Documents tree as a script, from dist\\ as a frozen exe, or from
    C:\\Program Files\\ after an install (where writing beside the binary would
    fail outright for a normal user).

    Last resort is ROOT_DIR, reachable only if %LOCALAPPDATA% itself cannot be
    created -- keeps the app running rather than crashing at import time.
    """
    if _os.name == "nt":
        base = _os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    elif _sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = _os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    d = Path(base) / "JS8MapChat" / "FastChat"
    try:
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:
        return ROOT_DIR


DATA_DIR = _resolve_data_dir()


# ------------------------------------------------------------------------------
# SHARED_DIR -- the JS8Map <-> FastChat mailbox.
# ------------------------------------------------------------------------------
# Geometry (portable / dev layout):
#   JS8MapChat\
#     JS8FastChat Ver 1.75\          <- the app's folder
#     JS8Map Ver 1.75\
#     JS8MapChat Shared Ver 1.5\    <- SHARED_DIR (sibling)
#
# RESOLUTION LOGIC BELOW IS UNCHANGED BY THE #26 FIX. It was proven working on
# 2026-07-13 (one js8fastchat_alive.json, in the shared folder, both apps
# agreeing). The two anchors it walks up from are the same two as before:
# _anchor_dir() and the source dir. Anchor COUNT is ticket #35's business, not
# this patch's -- do not "harmonise" it here.

# ------------------------------------------------------------------------------
# #35: the shared-folder resolution ALGORITHM now lives in ONE place --
# shared_resolver.py, a byte-identical twin of the copy in JS8Map's tree (the
# build hash-gate refuses to build if they differ). This file keeps only a THIN
# ADAPTER: it hands the shared resolver FastChat's own anchors and converts the
# result to FastChat's contract (a Path; ROOT_DIR substituted on total failure
# so the SHARED_DIR / name path-joins below never crash).
#
# The two anchors FastChat historically walked up from -- _anchor_dir() and
# _SOURCE_DIR -- are passed as exe_dir and app_dir. DATA_DIR (defined above) is
# passed as the third anchor; it was NOT in FastChat's old 2-anchor walk, so
# this WIDENS FastChat's search to match JS8Map's union. Wider search can only
# find the folder in more layouts, never fewer. See architecture doc #35 / s17.
from .shared_resolver import resolve_shared_dir as _shared_resolve_shared_dir


def _resolve_shared_dir() -> tuple[Path, str]:
    """FastChat's adapter over the shared resolver. Returns (Path, rule).

    On total failure the shared core returns ('', rule); FastChat substitutes
    ROOT_DIR -- unchanged behaviour from before #35 -- so downstream
    SHARED_DIR / name expressions still produce a Path instead of crashing.
    """
    path_str, rule = _shared_resolve_shared_dir(
        str(_anchor_dir()),   # exe_dir  (FastChat's stable anchor)
        str(_SOURCE_DIR),     # app_dir  (the source tree)
        str(DATA_DIR),        # data_dir (extra anchor; widens to JS8Map's union)
    )
    if not path_str:
        return ROOT_DIR, "FAILED (fell back to ROOT_DIR -- the JS8Map handoff will NOT work)"
    return Path(path_str), rule


SHARED_DIR, _SHARED_DIR_RULE = _resolve_shared_dir()


# ------------------------------------------------------------------------------
# Resolution log -- ticket #42's lesson, applied to FastChat. (H4 marker string:
# _fc_log_data_resolution)
# ------------------------------------------------------------------------------
def _fc_log_data_resolution() -> None:
    """Announce, once per launch, WHERE this process decided its folders are.

    print() goes nowhere: the VBS launcher runs pythonw.exe hidden and a
    --noconsole PyInstaller build has no console at all. A warning nobody can
    see is a silent failure (ticket #7). So this goes to a FILE, and it logs on
    SUCCESS as well as failure -- H1: the old log stayed EMPTY during the F:
    ghost incident because the resolver never failed, it succeeded at the wrong
    answer. Never let a folder decision be invisible again.
    """
    import datetime as _dt
    try:
        stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        frozen = bool(getattr(_sys, "frozen", False))
        lines = [
            f"{stamp}  [FastChat] DATA DIR   = {DATA_DIR}",
            f"{stamp}  [FastChat] SHARED DIR = {SHARED_DIR}",
            f"{stamp}  [FastChat]   rule     : {_SHARED_DIR_RULE}",
            f"{stamp}  [FastChat]   version  : {APP_VERSION}  frozen={frozen}",
            f"{stamp}  [FastChat]   ROOT_DIR : {ROOT_DIR}",
            f"{stamp}  [FastChat]   _SOURCE  : {_SOURCE_DIR}",
        ]
        with (DATA_DIR / "shared_resolve.log").open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception:
        pass   # logging must never take the app down


_fc_log_data_resolution()


# ------------------------------------------------------------------------------
# FastChat's OWN files. DATA_DIR, not ROOT_DIR. (Ticket #26)
# ------------------------------------------------------------------------------
CONFIG_PATH = DATA_DIR / "mjs_js8fastchat_3_2_config.json"
LEGACY_CONFIG_PATH = DATA_DIR / "mjs_js8maps_3_0_config.json"
LAYOUT_PATH = DATA_DIR / "js8fastchat_layout_3_2.json"
DEBUG_LOG = DATA_DIR / "js8fastchat_3_2_debug.log"
TX_HISTORY_PATH = DATA_DIR / "js8fastchat_tx_history_3_3.json"
CAPTURE_HISTORY_PATH = DATA_DIR / "js8fastchat_capture_3_4.json"
CAPTURE_PER_CALL_CAP = 400

GROUP_ALL = "All Groups"
CALL_ALL = "Show All"
DEFAULT_GROUPS = [GROUP_ALL, "@ALLCALL", "@AMRRON", "@HRMS", "@MAGNET", "@ARES", "@RACES", "@JS8"]
DEFAULT_MACROS = ["@ALLCALL QUERY CALL", "@AMRRON QUERY CALL", "@HRMS SNR?", "@MAGNET SNR?", "SNR?", "@AMRRON SNR?"]
DEFAULT_MSG_WATCH_WORDS = ["EMERGENCY", "POWER OUTAGE", "MEDICAL", "NEED RELAY", "NO POWER", "EVACUATE", "WELFARE CHECK"]

TIME_FILTERS = {
    "Last 15 minutes": 0.25,
    "Last 30 minutes": 0.5,
    "Last 1 hour": 1.0,
    "Last 2 hours": 2.0,
    "Last 4 hours": 4.0,
    "Last 12 hours": 12.0,
    "Last 24 hours": 24.0,
    # "All available" (unbounded, hours=None) REMOVED. It meant "no time
    # limit", which was harmless only while JS8Map wiped `spots` at every
    # launch -- it could reach no further back than the current session.
    # Since 270ae90 kept history across restarts it spans up to the full
    # 7-day retention, and it was the one window that ignored the
    # operator's stated 24-hour ceiling. Both apps now offer the same
    # seven bounded windows.
    # Code paths that special-cased hours=None (activity_reader.
    # cutoff_string returning None, and the `windowed` test in
    # display_rows) are now unreachable but left in place -- they are
    # harmless and removing them is unrelated surgery.
}

# Renamed from "Today", which read as "since midnight" -- a window that
# shrinks through the day. This entry has ALWAYS meant a fixed 24 hours.
# Saved configs still holding the old label are migrated on load; without
# this the value fails the TIME_FILTERS check in app_config.load_config()
# and the operator is silently dropped back to "Last 30 minutes".
# JS8Map carries the same alias -- keep the two in step.
LEGACY_TIME_FILTER_ALIASES = {
    "Today": "Last 24 hours",
    # Retired above. Migrated to the widest remaining window rather than
    # letting it fail validation, which would silently reset anyone using
    # it back to "Last 30 minutes".
    "All available": "Last 24 hours",
}
UI_SCALE_PRESETS = {"Compact 90%": 0.90, "Normal 100%": 1.00, "Large 115%": 1.15}
API_MODES = ["Legacy 2.x Compatible", "Improved / 3.x"]
SPEEDS = ["Slow", "Normal", "Fast", "JS8 40", "JS8 60"]
JS8_SPEED_CODES = {"Normal": 0, "Fast": 1, "JS8 40": 2, "Turbo": 2, "Slow": 4, "JS8 60": 8}
JS8_SPEED_DURATIONS = {"Slow": "about 30 sec TX", "Normal": "about 15 sec TX", "Fast": "about 10 sec TX", "JS8 40": "about 6 sec TX", "JS8 60": "about 4 sec TX"}

LIVE_RIG_GREEN = "#008f3a"
RX_HB_TILE_COLOR = "#ffec7a"
WATCHED_TILE_COLOR = "#b8860b"

# DEAD (v1.6, confirmed by dead_code_scan.py across all 42 live .py files):
#   FONT_BOOST    -- superseded by UI_SCALE_PRESETS (the UI Scale feature).
#                    Nothing applies it. Delete at code review.
#   GROUP_COLORS  -- NEVER WIRED UP. FastChat does not colour group tags at all.
#                    The group colours the operator sees are JS8Map's, and they
#                    are USER-ASSIGNED (JS8Map takes group_colors as a parameter
#                    from js8_groups.json). Not one of the twelve hex values
#                    below appears anywhere in JS8Map.py. ui/design_tokens.py
#                    carries a third orphan pair (JS8MAP_ACCENTS/JS8MAP_BUCKETS)
#                    from the same abandoned attempt.
#                    Colouring FastChat's group tags to match the map is a
#                    FEATURE, not a fix. It is ticket #30, and it is post-release.
# Both are kept only so no import can break before the soak test. Delete them
# during the code-review pass, once git (#8) exists to revert with.
FONT_BOOST = 1.28
GROUP_COLORS = {"@AMRRON": "#e83218", "@ARES": "#1e88e5", "@EMCOMM": "#00b050", "@FIELDDAY": "#4285f4", "@HRMS": "#9c27b0", "@MAGNET": "#00bcd4", "@MARS": "#e91e63", "@PREPNET": "#f57c00", "@RACES": "#fb8c00", "@SKYWARN": "#e0c300", "@ALLCALL": "#64748b", "@HB": "#ffec7a"}

JS8CALL_INI_CANDIDATES = [
    Path.home() / "AppData" / "Roaming" / "JS8Call" / "JS8Call.ini",
    Path.home() / "AppData" / "Local" / "JS8Call" / "JS8Call.ini",
    Path.home() / "Library" / "Preferences" / "JS8Call.ini",
    Path.home() / ".config" / "JS8Call.ini",
    Path.home() / ".config" / "JS8Call" / "JS8Call.ini",
    Path.home() / ".local" / "share" / "JS8Call" / "JS8Call.ini",
]

FCC_DB_CANDIDATES = [r"C:\FCC_DB\ham.db", r"C:\hamdb\ham.db", r"C:\FCC DB\ham.db", r"D:\FCC_DB\ham.db", r"D:\hamdb\ham.db", r"D:\FCC DB\ham.db"]
CANADIAN_DB_FILENAMES = ["canadian_callsigns.db", "canada_callsigns.db", "canadian.db", "canada.db", "ve_callsigns.db", "rac.db", "ham_canada.db", "ca_ham.db"]
CANADIAN_DB_CANDIDATES = [r"C:\Canadian_DB", r"C:\Canada_DB", r"C:\hamdb", r"C:\FCC_DB", r"D:\Canadian_DB", r"D:\Canada_DB", r"D:\hamdb", r"D:\FCC_DB"]
CANADIAN_CALL_PREFIXES = ("VA", "VB", "VC", "VD", "VE", "VF", "VG", "VO", "VX", "VY", "XJ", "XK", "XL", "XM", "XN", "CF", "CG", "CH", "CI", "CJ", "CK", "CY", "CZ")
PROTOCOL_WORDS = {"HEARTBEAT", "SNR", "MSG", "ACK", "QUERY", "FROM", "TO", "CALL", "ALLCALL", "GRID", "INFO", "STATUS", "HEARING", "HB", "ID", "RSNR"}

# ==============================================================================
# THE *_PATH CONSTANTS ARE GONE -- READ THIS BEFORE ADDING THEM BACK (2026-07-18)
# ==============================================================================
# INTENT_PATH, RAISE_PATH and SERVER_PATH used to be defined below, as
# SHARED_DIR / <filename>. They were NEVER the contract between JS8Map and
# FastChat -- nothing in the application read them (verified by dead_code_scan.py
# across all 42 live .py files). Their sole consumer was the debug script
# verify_shared_dir.py, which was deleted in Phase 6 (commit 7d7abec), so the
# three constants were deleted with it (Phase 7, task 4; grep-clean at removal).
#
# The REAL contract is intent_poller._search_dirs(), which searches, in order:
#     1. SHARED_DIR                       (JS8Map 1.6 writes here)
#     2. %LOCALAPPDATA%\JS8Map, %APPDATA%\JS8Map, MJsJS8Map, MJ_JS8Map
#                                         (where a FROZEN JS8Map.exe writes)
# It imports only the *_FILENAME constants, never a pre-joined path.
#
# WHY THIS WARNING STAYS: on 2026-07-12 those three constants were read as if
# they were the contract. JS8Map was "fixed" to write to them, which MOVED the
# intent and raise files out of %LOCALAPPDATA%\JS8Map -- where the poller was
# actually finding them -- and broke two buttons that had worked for weeks. The
# constants looked authoritative and were not. DO NOT reintroduce pre-joined
# SHARED_DIR paths here; use intent_poller's finders.
#
# NOTE (2026-07-13, ticket #26 patch): ALIVE_PATH at the bottom of this file is
# NOT dead, and that was CHECKED, not assumed. JS8Map.py:4101 reads the sentinel
# from its own resolved shared dir, and on disk there is exactly one
# js8fastchat_alive.json, in the shared folder. fastchat_heartbeat.py writes via
# SHARED_DIR (line 77); its Path(__file__) line is an isolated-test FALLBACK,
# reached only if BOTH imports of constants fail. ALIVE_PATH is live. It agrees
# with reality. Leave it alone.
# ==============================================================================

# Phase 4: JS8Map -> FastChat handoff intent file.
# JS8Map writes this file; FastChat reads it; FastChat NEVER writes/deletes it.
# Format: {"callsign": "KO4BIA", "timestamp": "2026-06-18T16:30:00Z"} (ISO 8601 UTC)
# FastChat considers an intent "fresh" if its UTC timestamp is within this window.
INTENT_FILENAME = "js8fastchat_intent.json"
INTENT_FRESH_SECONDS = 120  # ignore intents older than this (generous for clock skew)
INTENT_POLL_MS = 2000       # how often FastChat checks for a new intent (milliseconds)

# App-switch button (both apps): JS8Map -> FastChat "raise my main window" signal.
# Same locked architecture as the intent file: JS8Map is the sole writer,
# FastChat only reads. Unlike the intent file this carries no callsign -- it
# just asks FastChat to bring its MAIN console window to the front. Polled on
# the same tick as the intent file by the existing IntentPoller.
# Format: {"timestamp": "2026-06-18T16:30:00Z"} (ISO 8601 UTC)
RAISE_FILENAME = "js8map_raise_fastchat.json"

# App-switch button (FastChat -> JS8Map): port-discovery file.
# JS8Map binds a RANDOM free port at startup, so FastChat (a separate process)
# can't guess it. JS8Map publishes its chosen port into this file in the same
# folder; FastChat reads it fresh each time the button is clicked and POSTs to
# http://127.0.0.1:<port>/raise_window. Rewritten on every JS8Map launch, so a
# restart on a new port is picked up automatically. Same locked architecture:
# JS8Map is the sole writer, FastChat only reads.
# Format: {"port": 49732, "timestamp": "2026-06-18T16:30:00Z"} (ISO 8601 UTC)
SERVER_FILENAME = "js8map_server.json"

# JS8Map -> FastChat outgoing-TX handoff.
# Sends fired from the JS8Map UI go straight to JS8Call and NEVER pass through
# FastChat's own TX path (record_outgoing_tx), so without this they're invisible
# in the FastChat conversation -- you'd see the other station's reply but not the
# query you sent. JS8Map appends a record of each directed frame IT transmits to
# this file; FastChat merges those into the chat's "sent" (gold) side alongside
# its own TX log. Same locked architecture as the intent/raise/server files:
# JS8Map is the SOLE writer (append + trim); FastChat ONLY reads, never writes or
# deletes it.
# IMPORTANT: write timestamps as UTC with a literal " UTC" suffix (e.g.
# "2026-06-21 18:13:40 UTC"), matching FastChat's own TX log. This keeps the
# chat's UTC interleave correct and dodges the naive-local-vs-UTC sort bug.
# Format: {"rows": [
#   {"timestamp": "2026-06-21 18:13:40 UTC", "from": "KW3KW", "to": "KA2YNT",
#    "context": "HEARING?", "text": "KA2YNT HEARING?"}, ...]}
OBSERVED_TX_FILENAME = "js8map_outgoing_tx.json"
OBSERVED_TX_PATH = SHARED_DIR / OBSERVED_TX_FILENAME

# Phase 10: FastChat -> JS8Map presence sentinel.
# FastChat writes this file on startup, refreshes it every 5 s, and deletes it
# on clean exit. JS8Map polls it every 3 s; if the file is missing OR its "ts"
# field is older than ALIVE_STALE_SECONDS the FastChat button in the JS8Map
# callsign popup is grayed out. FastChat is the SOLE WRITER; JS8Map ONLY READS.
# Format: {"pid": 12345, "version": "1.75", "ts": 1720000000.0}
ALIVE_FILENAME      = "js8fastchat_alive.json"
ALIVE_PATH          = SHARED_DIR / ALIVE_FILENAME
ALIVE_STALE_SECONDS = 15   # must match fastchat_heartbeat.ALIVE_STALE_SECONDS
