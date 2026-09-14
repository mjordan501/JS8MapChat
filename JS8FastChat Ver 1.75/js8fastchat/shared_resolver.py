# shared_resolver.py
# =============================================================================
# JS8MapChat -- THE ONE shared-folder resolver.  Ticket #35.
# =============================================================================
# TWO PROGRAMS, ONE FOLDER.  JS8Map and JS8FastChat never share a process and
# never import each other; their entire contract is that they INDEPENDENTLY
# compute the SAME shared mailbox folder.  Until now each carried its own copy
# of the resolution algorithm, and the two copies had drifted in four ways
# (reversed env-var order, 4 walk-up anchors vs 2, a legacy pointer step in one
# but not the other, different last resorts).  They AGREED on the developer's
# machine and would have SPLIT on a stranger's -- the failure this whole project
# is built to prevent.
#
# This module is that algorithm, written ONCE.  It is deliberately dependency-
# free (os, sys, pathlib only) so it drops cleanly into JS8Map.py's flat
# namespace AND the js8fastchat package, and bundles into either PyInstaller
# onefile build with no .spec work.
#
# IT IS SHIPPED AS TWO BYTE-IDENTICAL COPIES, one in each app's tree, and the
# build scripts HASH BOTH AND REFUSE TO BUILD IF THEY DIFFER.  That is what
# closes #35: not "one file on disk" (which would create the first-ever shared
# runtime dependency between two apps whose stability comes from independence,
# and would be duplicated into every backup/ghost tree -- H1/H1b), but "two
# copies that CANNOT drift unnoticed."  A failing build cannot be ignored; a
# shared file can still be edited wrong.
#
# ---------------------------------------------------------------------------
# CONTRACT (read before you change anything):
#
#   resolve_shared_dir(exe_dir, app_dir, data_dir) -> (path_str, rule_str)
#
#   * Returns a PLAIN STRING path, or '' on total failure, plus a rule string
#     for the #42 log.  '' is load-bearing: JS8Map's callers do
#     `_fc_shared_dir() or _DATA_DIR` and `if not shared: return`.  DO NOT make
#     this return a fallback path -- that silently reintroduces the exact bug
#     ticket #40 is about (a return-value contract changing under callers who
#     were relying on the old one).
#   * The caller passes in its OWN anchors.  This module does NOT compute
#     _EXE_DIR / _APP_DIR / _DATA_DIR -- those are each app's frozen/script
#     business and correctly differ.  The module owns only the SEARCH, which is
#     the part that must be identical.
#   * per_user_shared_dir() CREATES the folder (JS8Map and FastChat's own
#     resolvers both do).  per_user_shared_dir_path() below is the compute-only
#     twin for READERS: same path, NEVER created (a reader must not create the
#     mailbox).  The intent poller uses the compute-only one.  Phase 7 task 3
#     moved the path CALC here so the two can't drift; the create/no-create
#     SPLIT is deliberate and stays.  See architecture doc s17.
#
# MUST STAY IDENTICAL ACROSS BOTH COPIES.  The build hash-gate enforces it.
# =============================================================================

import os
import sys
from pathlib import Path

# The accepted shared-folder names, canonical first.  v1.6 took the version OUT
# of the canonical name so a version bump is no longer a breaking change to the
# contract; the old versioned name is still accepted so existing installs keep
# working.  MUST match both apps' historical *_SHARED_DIR_NAMES.
SHARED_DIR_NAMES = ("JS8MapChat Shared", "JS8MapChat Shared Ver 1.5")

# How far up the tree the walk-up will climb looking for a sibling shared
# folder.  Was _WALK_UP_MAX_LEVELS / _FC_WALK_UP_MAX_LEVELS -- identical in both.
WALK_UP_MAX_LEVELS = 5

# Env-var override names, checked in THIS ORDER.  Canonical (product-name) var
# first; the legacy JS8FASTCHAT_ name second.  (JS8Map used to check them in the
# reverse order -- divergence #1.  Product-name-wins is the resolution.)
ENV_OVERRIDE_VARS = ("JS8MAPCHAT_SHARED_DIR", "JS8FASTCHAT_SHARED_DIR")

# Legacy escape-hatch pointer file (JS8Map carried this; FastChat did not --
# divergence #3).  Kept in the shared algorithm: it only fires when no sibling
# is found AND the file exists, so giving FastChat the same escape hatch can
# only help an odd layout and costs nothing when the file is absent.
LEGACY_POINTER_FILENAME = "js8map_writer_path.txt"


def find_shared_by_walking_up(start_dir: str):
    """Walk up from start_dir (inclusive), at each ancestor checking its direct
    children for a folder named in SHARED_DIR_NAMES.  Returns the path string of
    the first match, or None.  Bounded by WALK_UP_MAX_LEVELS.

    Identical algorithm to both apps' former walk-up finders; this is the copy
    that survives.
    """
    try:
        cur = os.path.abspath(start_dir)
    except Exception:
        return None
    for _ in range(WALK_UP_MAX_LEVELS):
        for _name in SHARED_DIR_NAMES:
            candidate = os.path.join(cur, _name)
            if os.path.isdir(candidate):
                return candidate
        parent = os.path.dirname(cur)
        if parent == cur:
            break   # filesystem root
        cur = parent
    return None


def per_user_shared_dir_path():
    """COMPUTE the per-user mailbox path.  NEVER creates anything.

    %LOCALAPPDATA%\\JS8MapChat\\Shared (and the OS-appropriate equivalent
    elsewhere).  This is the ONE place that path is spelled out; both the
    creating resolver below and the intent poller's non-creating reader call
    THIS, so the two can no longer drift apart (Phase 7 task 3).

    Returns a string path, or '' if the base folder cannot even be computed.
    READERS (the intent poller) use this directly: a reader must not create
    the mailbox, so there is no makedirs here and never will be.
    """
    try:
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA") \
                   or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        elif sys.platform == "darwin":
            base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
        else:
            base = os.environ.get("XDG_DATA_HOME") \
                   or os.path.join(os.path.expanduser("~"), ".local", "share")
        return os.path.join(base, "JS8MapChat", "Shared")
    except Exception:
        return ""


def per_user_shared_dir():
    """The installed-build home for the mailbox, CREATED if absent.

    This is the install-location-independent rendezvous both apps compute the
    same way, so they meet without either knowing where the other is installed.
    Needed because an installed .exe in Program Files has no sibling shared
    folder and could not write under {app} anyway.

    Returns a string path, or '' if even %LOCALAPPDATA% cannot be created.

    NOTE: this CREATES the folder -- it is for the apps' OWN resolvers (both
    are writers).  The path itself comes from per_user_shared_dir_path() above;
    the only thing added here is the makedirs.  Readers must use the
    compute-only function, never this one.
    """
    try:
        d = per_user_shared_dir_path()
        if not d:
            return ""
        os.makedirs(d, exist_ok=True)
        return d
    except Exception:
        return ""


def _read_legacy_pointer(base: str):
    """If base/js8map_writer_path.txt exists and points at a real directory,
    return that directory; else None.  Escape hatch only."""
    try:
        pf = os.path.join(base, LEGACY_POINTER_FILENAME)
        if os.path.isfile(pf):
            with open(pf, "r", encoding="utf-8") as fh:
                txt = fh.read().strip().strip('"')
            if txt:
                # tolerate the pointer naming a FILE inside the dir
                if os.path.splitext(txt)[1] and not os.path.isdir(txt):
                    txt = os.path.dirname(txt)
                if os.path.isdir(txt):
                    return txt
    except Exception:
        pass
    return None


def resolve_shared_dir(exe_dir: str, app_dir: str, data_dir: str):
    """Resolve the JS8Map <-> FastChat shared mailbox.  THE one algorithm.

    Anchors are passed in by the caller (each app computes its own exe/app/data
    dirs -- that part legitimately differs between the apps and between frozen
    and script mode).  The SEARCH performed over those anchors is what must be
    identical, and it lives here.

    Order:
      1) env override -- JS8MAPCHAT_SHARED_DIR, then JS8FASTCHAT_SHARED_DIR
      2) walk up from (exe_dir, app_dir, data_dir, cwd) for a sibling shared
         folder -- the union of what the two apps used to search, in JS8Map's
         order.  exe_dir is the stable anchor (dist\\ when frozen); the rest are
         fallbacks for unusual layouts.
      3) legacy pointer file, across the same anchors -- escape hatch only
      4) per-user mailbox, created if absent -- the installed-build path
      5) '' -- total failure.  Caller's no-op-on-empty behaviour applies.
         (JS8Map returns this as-is; FastChat's adapter substitutes ROOT_DIR so
         its SHARED_DIR / name path-joins don't crash.)

    Returns (path_str, rule_str).  path_str is '' ONLY in case 5.
    """
    # 1) explicit override (either var name; canonical first)
    for var in ENV_OVERRIDE_VARS:
        env = os.environ.get(var)
        if env and env.strip() and os.path.isdir(env.strip()):
            return env.strip(), f"env-override {var}"

    # anchors: union of both apps', JS8Map's order, cwd last.  De-dup while
    # preserving order so a repeated anchor doesn't waste a walk.
    _seen = set()
    anchors = []
    for a in (exe_dir, app_dir, data_dir, os.getcwd()):
        if a and a not in _seen:
            _seen.add(a)
            anchors.append(a)

    # 2) walk up from each anchor
    for base in anchors:
        found = find_shared_by_walking_up(base)
        if found:
            return found, f"walk-up from {base}"

    # 3) legacy pointer file, same anchors
    for base in anchors:
        ptr = _read_legacy_pointer(base)
        if ptr:
            return ptr, f"legacy pointer file {os.path.join(base, LEGACY_POINTER_FILENAME)}"

    # 4) per-user mailbox (installed-build path), created if absent
    peruser = per_user_shared_dir()
    if peruser:
        return peruser, "per-user mailbox (NO SIBLING FOUND)"

    # 5) total failure -- '' is the contract
    return "", "FAILED - no shared folder anywhere"
