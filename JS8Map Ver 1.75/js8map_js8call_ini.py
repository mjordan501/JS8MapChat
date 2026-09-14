#!/usr/bin/env python3
"""JS8Call.ini reading: locate the .ini and parse highlight callsigns + @groups.

Extracted verbatim from JS8Map.py (behaviour unchanged; location only).
Pure stdlib leaf — imports nothing from the monolith."""
from __future__ import annotations

import os
import re
import configparser


def _js8call_ini_candidates():
    """Candidate JS8Call.ini locations, resolved AT CALL TIME.
    Env vars (APPDATA/LOCALAPPDATA) and ~ are read on each call so a frozen
    build that lacked them at import still finds the file. Order = priority."""
    appdata = os.environ.get('APPDATA', '')
    local   = os.environ.get('LOCALAPPDATA', '')
    home    = os.path.expanduser('~')
    return [
        # Windows - standard JS8Call (Roaming + Local, env-var and ~ expansion)
        os.path.join(appdata, 'JS8Call',          'JS8Call.ini'),
        os.path.join(local,   'JS8Call',          'JS8Call.ini'),
        os.path.join(home, 'AppData', 'Roaming', 'JS8Call',          'JS8Call.ini'),
        os.path.join(home, 'AppData', 'Local',   'JS8Call',          'JS8Call.ini'),
        # Windows - JS8Call-Improved (hyphen variant, both Roaming and Local)
        os.path.join(appdata, 'JS8Call-Improved', 'JS8Call.ini'),
        os.path.join(local,   'JS8Call-Improved', 'JS8Call.ini'),
        os.path.join(home, 'AppData', 'Roaming', 'JS8Call-Improved', 'JS8Call.ini'),
        os.path.join(home, 'AppData', 'Local',   'JS8Call-Improved', 'JS8Call.ini'),
        # Windows - JS8Call_Improved (underscore variant, both Roaming and Local)
        os.path.join(appdata, 'JS8Call_Improved', 'JS8Call.ini'),
        os.path.join(local,   'JS8Call_Improved', 'JS8Call.ini'),
        os.path.join(home, 'AppData', 'Roaming', 'JS8Call_Improved', 'JS8Call.ini'),
        os.path.join(home, 'AppData', 'Local',   'JS8Call_Improved', 'JS8Call.ini'),
        # macOS
        os.path.join(home, 'Library', 'Preferences', 'JS8Call.ini'),
        # Linux - flat in .config (JS8Call 3.02 / Improved)
        os.path.join(home, '.config', 'JS8Call.ini'),
        # Linux - in JS8Call subfolder (standard JS8Call 2.x)
        os.path.join(home, '.config', 'JS8Call', 'JS8Call.ini'),
        os.path.join(home, '.local', 'share', 'JS8Call', 'JS8Call.ini'),
    ]

_CS_RE = re.compile(r'^[A-Z0-9]{1,3}[0-9][A-Z]{1,4}(?:/[A-Z0-9]+)?$')

def find_js8call_ini() -> str:
    """Return path to JS8Call.ini if found, else empty string."""
    for p in _js8call_ini_candidates():
        if p and os.path.isfile(p):
            return p
    return ''

def read_js8call_highlights(ini_path: str = '') -> set:
    """
    Parse JS8Call.ini and return the secondary highlight callsign list.
    JS8Call stores UI highlight words as comma-separated strings in its INI.
    We search all keys for comma-separated callsign-like values, preferring
    keys whose name contains 'secondary', 'highlight', or 'word'.
    """
    path = ini_path or find_js8call_ini()
    if not path or not os.path.isfile(path):
        return set()
    calls: set = set()
    try:
        # Read raw to preserve encoding; prepend dummy section for configparser
        raw = open(path, encoding='utf-8', errors='replace').read()
        # configparser needs a default section if the file starts with a key before any [section]
        cfg = configparser.RawConfigParser(strict=False)
        cfg.read_string('[__root__]\n' + raw)
        best_score = 0
        best_calls: set = set()
        for section in cfg.sections():
            for key, value in cfg.items(section):
                if not value or len(value) < 3:
                    continue
                parts = [p.strip().upper() for p in value.split(',')]
                matched = {p for p in parts if _CS_RE.match(p) and len(p) >= 3}
                if len(matched) < 1:
                    continue
                # Score: prefer keys with relevant names
                name_score = sum(1 for w in ('secondary','highlight','word','call','watch')
                                 if w in key.lower())
                total_score = len(matched) + name_score * 3
                if total_score > best_score:
                    best_score = total_score
                    best_calls = matched
                calls.update(matched)
        # If we found a clearly named key, use only that; otherwise return all found
        return best_calls if best_calls else calls
    except Exception:
        return set()

def _resolve_js8call_ini(override: str = '') -> str:
    """Return the JS8Call.ini path: explicit override if given, else the
    platform default. Existence is checked by the caller."""
    if override:
        return os.path.expanduser(override)
    import sys
    home = os.path.expanduser('~')
    if sys.platform.startswith('win'):
        base = os.environ.get('LOCALAPPDATA') or os.path.join(home, 'AppData', 'Local')
        return os.path.join(base, 'JS8Call', 'JS8Call.ini')
    if sys.platform == 'darwin':
        return os.path.join(home, 'Library', 'Preferences', 'JS8Call.ini')
    return os.path.join(home, '.config', 'JS8Call.ini')

def read_js8call_groups(ini_path_override: str = ''):
    """Read the Callsign Groups list from JS8Call.ini.
    Returns (groups_list, ini_path). groups_list is a de-duped list of
    '@GROUP' strings (uppercased), or [] if the file or a groups line is not
    found. ini_path is the path that was tried (for status/diagnostics)."""
    path = _resolve_js8call_ini(ini_path_override)
    if (not path or not os.path.exists(path)) and not ini_path_override:
        # Single-path resolver missed (frozen build w/o LOCALAPPDATA, or a
        # Roaming / *_Improved layout) -- fall back to the call-time scan.
        found = find_js8call_ini()
        if found:
            path = found
    if not path or not os.path.exists(path):
        return [], path
    _tok_re = re.compile(r'@[A-Z0-9/]{1,20}$')
    def _norm(t):
        # JS8Call-Improved writes group names double-@ prefixed (@@HRMS);
        # collapse any leading run of '@' to a single '@' before matching.
        t = t.strip().strip('"').upper()
        while t.startswith('@@'):
            t = t[1:]
        return t
    candidates = []   # (score, [groups])
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            section = ''
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line[0] == '[' and line[-1] == ']':
                    section = line[1:-1]
                    continue
                if line[0] in ';#' or '=' not in line:
                    continue
                key, _, val = line.partition('=')
                key = key.strip()
                val = val.strip().strip('"')
                if '@' not in val:
                    continue
                toks = [_norm(t)
                        for t in val.replace(';', ',').split(',') if t.strip()]
                if not toks:
                    continue
                grp_toks = [t for t in toks if _tok_re.match(t)]
                # Require the value to be *entirely* group tokens — rejects
                # emails, paths, or anything that merely contains an '@'.
                if not grp_toks or len(grp_toks) != len(toks):
                    continue
                seen = []
                for t in grp_toks:
                    if t not in seen:
                        seen.append(t)
                # Prefer the ACTIVE [Configuration] MyGroups over per-radio
                # profile copies under [MultiSettings] (keys like
                # 'IC-7300\\Configuration\\MyGroups'), which can be stale.
                is_active = (section == 'Configuration' and key == 'MyGroups')
                score = ((1000 if is_active else 0)
                         + (10 if 'group' in key.lower() else 0)
                         + len(seen))
                candidates.append((score, seen))
    except Exception:
        return [], path
    if not candidates:
        return [], path
    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates[0][1], path

_GROUP_AUTO_PALETTE = [
    '#CC2200', '#00AA44', '#0077CC', '#FF8800', '#9933CC',
    '#00BBAA', '#CC6600', '#CCAA00', '#CC0088', '#4488FF',
    '#7CB342', '#D81B60', '#3949AB', '#00897B', '#F4511E',
]

