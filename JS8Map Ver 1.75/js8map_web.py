#!/usr/bin/env python3
"""
js8map_web.py  —  JS8MapChat 1.75 web/render substrate (extracted from JS8Map.py).

Phase 6 / W1: seeds this module with build_station_list, the pure station-list
builder consumed by render_html. Leaf deps only (js8map_util, math); no back-import
into the monolith. The HTTP server (_ensure_server/open_in_browser) and render
helpers land here in later commits.
"""

import math
from js8map_util import _base, grid_to_latlon, norm_call
import os
import json
import shutil
import socket
from datetime import datetime, timezone as _dt_timezone
from js8map_runtime import (APP_DIR as _APP_DIR, DATA_DIR as _DATA_DIR,
                            EXE_DIR as _EXE_DIR, load_config as _load_config,
                            DEFAULT_CONFIG as _DEFAULT_CONFIG,
                            APP_VERSION as _APP_VERSION)
from urllib.parse import quote as _urlquote
from js8map_theme import COLORS, LABEL_SIZE
from js8map_relay import (_armed_relay_ref, _armed_relay_pos_ref, _relay_committed_ref,
    _relay_active_ref, _relay_confidence, _relay_age_min, _relay_anim_ref,
    _relay_dest_timer, _relay_failed_vias_ref, _relay_msg_ref, _relay_outcome_ref,
    _relay_return_timer, _relay_tx_frame_count, _relay_tx_last_frame_ts, _relay_tx_timer,
    _relay_via_last_frag_ts, _relay_watchdog_timer, _relay_yes_list_ref, _relay_yes_ts_ref)
import http.server
import threading
import time
import webbrowser
from js8map_platform import (focus_existing_map_window as _focus_existing_map_window,
    launch_map_window as _launch_map_window)
from js8map_client import _last_seen_offset


def build_station_list(stations_heard: dict, stations_hearing_me: dict,
                        fcc_info: dict = None, via_heartbeat: set = None,
                        heard_by: dict = None, station_hears: dict = None,
                        snr_of_me: dict = None, inbox_senders: set = None,
                        watched_calls: set = None,
                        pending_msg_senders: set = None,
                        group_colors: dict = None) -> list:
    combined: dict = {}
    fcc_info            = fcc_info or {}
    via_heartbeat       = via_heartbeat or set()
    heard_by            = heard_by or {}
    station_hears       = station_hears or {}
    snr_of_me           = snr_of_me or {}
    inbox_senders       = inbox_senders or set()
    watched_calls       = watched_calls or set()
    pending_msg_senders = pending_msg_senders or set()
    group_colors        = group_colors or {}

    def _hb_list(call):
        return [{'by': r, 'snr': s, 'ts': t[:16]}
                for r, s, t in heard_by.get(call, [])]

    def _sh_list(call):
        return [{'call': to, 'snr': snr, 'ts': ts[:16]}
                for to, snr, ts in station_hears.get(call, [])]

    for call, info in stations_heard.items():
        pos = grid_to_latlon(info['grid'])
        if not pos:
            continue
        fcc = fcc_info.get(call, {})
        # snr_of_me entry = station reported our SNR = they definitively hear us
        # Check full callsign AND base (W3BFO/P → W3BFO) for SNR proof
        _snr_proof = snr_of_me.get(call)
        if _snr_proof is None and '/' in call:
            _snr_proof = snr_of_me.get(_base(call))
        _type = 'both' if _snr_proof is not None else 'heard'
        combined[call] = {
            'call': call, 'grid': info['grid'],
            'lat': pos[0], 'lon': pos[1],
            'snr': info['snr'], 'last_seen': info['last_seen'],
            'freq': info.get('freq', 0), 'offset': info.get('offset', 0),
            'speed': info.get('speed', 0), 'count': info.get('count', 1),
            'type': _type,
            'via_hb':        call in via_heartbeat,
            'approx':        info.get('approx', False),
            'heard_by':      _hb_list(call),
            'station_hears': _sh_list(call),
            'snr_of_me':     snr_of_me.get(call) if snr_of_me.get(call) is not None else snr_of_me.get(_base(call)),
            'snr_proof_ts':  info.get('snr_proof_ts', ''),
            'first_seen_ts': info.get('first_seen_ts', ''),
            'has_inbox':     call in inbox_senders,
            'has_pending_msg': call in (pending_msg_senders or set()),
            'watched':       call in watched_calls or _base(call) in watched_calls,
            'group_color':   (group_colors.get(call) or group_colors.get(_base(call)) or {}).get('color'),
            'group_name':    (group_colors.get(call) or group_colors.get(_base(call)) or {}).get('group'),
            'group_all':     (group_colors.get(call) or group_colors.get(_base(call)) or {}).get('all_groups', []),
            'is_new':        info.get('is_new', False),
            'fcc_name':  fcc.get('name', ''),
            'fcc_city':  fcc.get('city', ''),
            'fcc_state': fcc.get('state', ''),
            'fcc_class': fcc.get('class', ''),
        }

    for call, info in stations_hearing_me.items():
        pos = grid_to_latlon(info['grid'])
        if not pos:
            continue
        fcc = fcc_info.get(call, {})
        if call in combined:
            combined[call]['type'] = 'both'
            combined[call]['via_hb'] = combined[call]['via_hb'] or (call in via_heartbeat)
        else:
            combined[call] = {
                'call': call, 'grid': info['grid'],
                'lat': pos[0], 'lon': pos[1],
                'snr': info['snr'], 'last_seen': info['last_seen'],
                'freq': info.get('freq', 0), 'offset': info.get('offset', 0),
                'speed': info.get('speed', 0), 'count': info.get('count', 1),
                'type': 'hearing_me',
                'via_hb':        call in via_heartbeat,
                'approx':        info.get('approx', False),
                'heard_by':      _hb_list(call),
                'station_hears': _sh_list(call),
                'snr_of_me':     snr_of_me.get(call) if snr_of_me.get(call) is not None else snr_of_me.get(_base(call)),
                'has_inbox':     call in inbox_senders,
                'has_pending_msg': call in (pending_msg_senders or set()),
                'watched':       call in watched_calls or _base(call) in watched_calls,
                'group_color':   (group_colors.get(call) or group_colors.get(_base(call)) or {}).get('color'),
                'group_name':    (group_colors.get(call) or group_colors.get(_base(call)) or {}).get('group'),
                'group_all':     (group_colors.get(call) or group_colors.get(_base(call)) or {}).get('all_groups', []),
                'is_new':        info.get('is_new', False),
                'fcc_name':  fcc.get('name', ''),
                'fcc_city':  fcc.get('city', ''),
                'fcc_state': fcc.get('state', ''),
                'fcc_class': fcc.get('class', ''),
            }

    # Jitter stations that share the same FCC state-center grid (approx=True)
    # so their dots and labels don't stack on top of each other on the map.
    from collections import defaultdict
    coord_groups: dict = defaultdict(list)
    for st in combined.values():
        key = f"{st['lat']:.2f},{st['lon']:.2f}"
        coord_groups[key].append(st)
    for group in coord_groups.values():
        if len(group) < 2:
            continue
        for idx, st in enumerate(group[1:], 1):
            ring  = ((idx - 1) // 6) + 1
            angle = ((idx - 1) % 6) * (math.pi / 3)
            r     = ring * 0.35   # ~35km per ring — visible at country zoom
            st['lat'] = round(st['lat'] + r * math.cos(angle), 5)
            st['lon'] = round(st['lon'] + r * math.sin(angle), 5)
    return sorted(combined.values(), key=lambda x: x['call'])

# ── Phase 6 / W2: render + static-asset layer (extracted from JS8Map.py) ──

def _build_group_filter_opts(group_names: list) -> str:
    if not group_names:
        return ''
    return '\n'.join(
        f'<option value="{n}">{n}</option>'
        for n in sorted(group_names)
    )

def _build_group_css(stations: list) -> str:
    seen, lines = set(), []
    for s in stations:
        c = s.get('group_color')
        if not c or c in seen:
            continue
        seen.add(c)
        cls = 'grp-' + c.lstrip('#')
        lines.append(
            f'.station-label-group.{cls} .label-box{{'
            f'border-color:{c}!important;border-width:1.5px!important;'
            f'box-shadow:0 3px 14px rgba(0,0,0,.88),0 0 5px {c}55!important;}}'
        )
    return '\n'.join(lines)

# Folder holding the static frontend (web/ subfolder OR flat beside the app).
_HERE = _APP_DIR
WEB_DIR = (os.path.join(_HERE, 'web')
           if os.path.exists(os.path.join(_HERE, 'web', 'index.html'))
           else _HERE)


# Everything the map page needs on disk. The last five are what make JS8Map
# work with no internet: the Leaflet library itself (was pulled from a CDN),
# the world borders and state/province lines (were pulled from GitHub on every
# single map open), and the place names that replace what a tile backdrop used
# to tell you. Without these the offline map came up as a blank page.
_STATIC_ASSETS = (
    'app.css', 'app.js', 'index.html', 'JS8Map_icon.png', 'fastchat_icon.png',
    'leaflet.js', 'leaflet.css',
    'js8map_borders.json', 'js8map_admin1.json', 'js8map_places.json',
    'js8map_lakes.json',
)


def _find_asset(name: str) -> str:
    """Locate a static asset, preferring the bundled web folder.

    The big map-data files are installed alongside JS8Map.exe rather than
    packed into it: a one-file exe unpacks its whole payload to temp on every
    launch, and 1.8 MB of borders would be paid for on every start for data
    that never changes. So EXE_DIR is searched as well as WEB_DIR.
    """
    for base in (WEB_DIR, _EXE_DIR, _APP_DIR):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return os.path.join(WEB_DIR, name)      # caller's exists() check handles it


def _copy_static_assets(served_dir: str) -> None:
    """Ensure the frontend files are beside ham_map.html. Copies only when
    missing or stale (mtime/size differ) so it's cheap to call every render.
    If you ship the web files flat in the served dir already, this is a no-op."""
    for name in _STATIC_ASSETS:
        src = _find_asset(name)
        dst = os.path.join(served_dir, name)
        try:
            if not os.path.exists(src):
                continue  # frontend file not present here — nothing to copy
            if os.path.abspath(src) == os.path.abspath(dst):
                continue  # already in the served dir (flat layout) — no copy needed
            if (not os.path.exists(dst)
                    or os.path.getmtime(src) > os.path.getmtime(dst)
                    or os.path.getsize(src) != os.path.getsize(dst)):
                shutil.copy2(src, dst)
        except Exception:
            pass


def _tile_url(url: str, key: str) -> str:
    """Return a ready-to-use tile URL, or '' meaning 'draw no tiles'.

    No key returns '' on purpose. CARTO began serving unkeyed raster tiles
    with "API KEY REQUIRED" stamped across them, so asking for tiles without
    a key actively spoils the map. The bundled borders and place names give a
    complete map on their own; tiles are optional street detail on top.

    '{key}' anywhere in the URL is substituted, so a future provider that
    wants the key somewhere other than the query string is a settings change.
    Otherwise the key is appended as CARTO's documented 'key=' parameter.
    """
    url = (url or '').strip()
    key = (key or '').strip()
    if not url or not key:
        return ''
    if '{key}' in url:
        return url.replace('{key}', _urlquote(key, safe=''))
    if 'key=' in url:                 # operator already put it in the URL
        return url
    return url + ('&' if '?' in url else '?') + 'key=' + _urlquote(key, safe='')


def _asset_tag(served_dir: str) -> str:
    """Version+mtime token used to defeat browser caching of shipped assets."""
    stamp = 0
    try:
        for _n in _STATIC_ASSETS:
            _p = os.path.join(served_dir, _n)
            if os.path.exists(_p):
                stamp = max(stamp, int(os.path.getmtime(_p)))
    except Exception:
        pass
    return '%s-%d' % (_APP_VERSION, stamp)


def _tile_settings() -> dict:
    """Read the map-backdrop settings fresh each render, so a key entered in
    the settings window takes effect on the next Generate Map without a
    restart."""
    try:
        cfg = _load_config()
    except Exception:
        cfg = dict(_DEFAULT_CONFIG)
    key = cfg.get('map_tile_key', '')
    return {
        'tile_url_light':    _tile_url(cfg.get('tile_url_light', ''), key),
        'tile_url_dark':     _tile_url(cfg.get('tile_url_dark', ''), key),
        'tile_attribution':  cfg.get('tile_attribution', ''),
        'show_place_labels': bool(cfg.get('show_place_labels', True)),
    }


def render_html(stations_heard: dict, stations_hearing_me: dict,
                my_callsign: str, my_grid: str, output_path: str,
                refresh_secs: int = 0, fcc_info: dict = None,
                via_heartbeat: set = None, heard_by: dict = None,
                station_hears: dict = None, snr_of_me: dict = None,
                inbox_senders: set = None, watched_calls: set = None,
                pending_msg_senders: set = None,
                group_colors: dict = None, group_filter_names: list = None,
                js8call_host: str = '127.0.0.1',
                js8call_port: int = 2442,
                qsy_freq: float = 0,
                time_filter_label: str = 'Last 30 days',
                relay_paths: dict = None,
                unplotted_mutuals: list = None,
                tx_halt_enabled: bool = False,
                fit_exclusions: set = None) -> int:
    """Write boot.js (per-render data) + ham_map.html (static shell copy),
    refresh the /data.json cache, and return the station count.
    Signature is unchanged from v1.79_H so _generate_thread keeps working."""

    _FILTER_MS = {
        'Last 15 minutes': 15 * 60 * 1000, 'Last 30 minutes': 30 * 60 * 1000,
        'Last 1 hour':     60 * 60 * 1000, 'Last 2 hours':   120 * 60 * 1000,
        'Last 4 hours':   240 * 60 * 1000, 'Last 12 hours':  720 * 60 * 1000,
        'Last 24 hours': 1440 * 60 * 1000,
        'Today':         1440 * 60 * 1000,   # retired label, still honoured
    }
    window_ms = _FILTER_MS.get(time_filter_label, 30 * 60 * 1000)
    my_pos = (grid_to_latlon(my_grid) if my_grid else None) or (39.5, -98.35)

    stations = build_station_list(
        stations_heard, stations_hearing_me,
        fcc_info or {}, via_heartbeat or set(),
        heard_by or {}, station_hears or {},
        snr_of_me or {}, inbox_senders or set(),
        watched_calls or set(), pending_msg_senders or set(),
        group_colors or {})

    # Single timestamp shared by the boot payload and /data.json so the first
    # _pollData tick never sees a mismatch (preserves the v1.79_H fix).
    _ts  = datetime.now().strftime('%Y%m%d%H%M%S')
    _gen = datetime.now().strftime('%Y-%m-%d %H:%M')

    # ── The single injected object. Every former {token} is a key here. ──
    boot = {
        'callsign':          my_callsign or 'MY STATION',
        'grid':              my_grid or '????',
        'generated':         _gen,
        'generate_ts':       _ts,
        'refresh_secs':      refresh_secs,
        'my_lat':            my_pos[0],
        'my_lon':            my_pos[1],
        'window_ms':         int(window_ms),
        'js8call_host':      js8call_host,
        'js8call_port':      js8call_port,
        'tx_halt_enabled':   tx_halt_enabled,
        'qsy_freq':          (f'{qsy_freq/1e6:.3f}' if qsy_freq else ''),
        'stations':          stations,
        'relay_paths':       relay_paths or {},
        'fit_exclusions':    sorted(fit_exclusions or []),
        'unplotted_mutuals': list(unplotted_mutuals or []),
        'COLORS':            COLORS,          # single source of truth, unchanged
        'LABEL_SIZE':        LABEL_SIZE,
        'group_css':         _build_group_css(stations),
        'group_filter_opts': _build_group_filter_opts(group_filter_names or []),
    }
    # Map backdrop: optional tile URLs (empty unless the operator has a key)
    # plus the place-names preference. Read per render so a change in the
    # settings window shows up on the next Generate Map.
    boot.update(_tile_settings())

    served_dir = os.path.dirname(os.path.abspath(output_path))
    # Resilience: if the resolved output folder doesn't exist (stale config
    # path from a prior version), fall back to writing beside this script so
    # boot.js / ham_map.html never fail with "No such file or directory".
    if not os.path.isdir(served_dir):
        served_dir = _DATA_DIR
        output_path = os.path.join(_DATA_DIR, 'ham_map.html')
    _copy_static_assets(served_dir)

    # Cache stamp for the four map-data files app.js fetches by name. This
    # MUST be set before the payload below is built, or it never reaches the
    # page -- and it must be after the assets are copied, so the mtimes it
    # reads are the ones actually being served.
    boot['asset_tag'] = _asset_tag(served_dir)

    # JSON, then neutralize </script> and angle brackets so the data can sit
    # safely inside a <script> tag and inside HTML attributes.
    payload = (json.dumps(boot, separators=(',', ':'))
               .replace('<', r'\u003C').replace('>', r'\u003E'))

    # boot.js — the only generated file; pure data, loaded before app.js.
    with open(os.path.join(served_dir, 'boot.js'), 'w', encoding='utf-8') as fh:
        fh.write('window.JS8MAP_BOOT = ' + payload + ';\n')

    # ham_map.html — static shell, copied from index.html with a cache stamp
    # added to every local asset link.
    #
    # WHY: the browser caches app.js, app.css and the map data by filename.
    # After an update it happily keeps serving the OLD ones, so the operator
    # installs a new version, sees the previous map, and concludes the update
    # failed. That happened on every single test this session -- Ctrl+F5 was
    # needed each time -- and an operator will not know to do that.
    #
    # Each link gets ?v=<version>-<newest asset mtime>, so the address changes
    # whenever any shipped asset changes and the browser fetches fresh. Within
    # a version nothing changes, so normal caching still works.
    shell = _find_asset('index.html')
    if not os.path.exists(shell):
        shell = os.path.join(served_dir, 'index.html')
    if os.path.exists(shell):
        try:
            stamp = 0
            for _n in _STATIC_ASSETS:
                _p = os.path.join(served_dir, _n)
                if os.path.exists(_p):
                    stamp = max(stamp, int(os.path.getmtime(_p)))
            token = '%s-%d' % (_APP_VERSION, stamp)
            with open(shell, 'r', encoding='utf-8') as fh:
                html = fh.read()
            for _n in ('app.css', 'app.js', 'leaflet.css', 'leaflet.js', 'boot.js'):
                html = html.replace('"%s"' % _n, '"%s?v=%s"' % (_n, token))
            with open(output_path, 'w', encoding='utf-8') as fh:
                fh.write(html)
        except Exception:
            # If anything goes wrong, fall back to the plain copy that always
            # worked. A stale-cache risk is far better than no map at all.
            shutil.copy2(shell, output_path)

    # /data.json cache — unchanged from v1.79_H (browser polls this).
    _data_json_bytes[0] = json.dumps({
        'ts':                _ts,
        'stations':          stations,
        'relay_paths':       relay_paths or {},
        'unplotted_mutuals': list(unplotted_mutuals or []),
        'window_ms':         int(window_ms),
        'group_names':       sorted(group_filter_names or []),
        'armed_target':      _armed_relay_ref[0],
        'armed_target_pos':  _armed_relay_pos_ref[0],
        'relay_committed':   _relay_committed_ref[0],
    }, separators=(',', ':')).encode()

    return len(stations)


_data_json_bytes = [b'{}']   # [0]=latest serialized /data.json payload, served live to browser


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(('', 0))
        return s.getsockname()[1]


# ── Phase 6 / W3: localhost HTTP server + browser launcher (extracted from JS8Map.py) ──
# Callbacks/holders below are injected by HamMapApp via wire(); the request handlers
# read them live. TX-mode holders and the FastChat shared-dir provider stay owned by
# the monolith and are passed by reference.
_relay_db_ref            = None
_focus_callback          = None
_refresh_callback        = None
_relay_stage_callback    = None
_popup_tx_callback       = None
_halt_tx_callback        = None
_fit_exclusion_callback  = None
_tx_mode_ref             = ['manual']    # holder ref injected by wire(); read [0] live
_tx_halt_ref             = [False]       # holder ref injected by wire(); read [0] live
_shared_dir_provider     = lambda: None  # provider injected by wire()

_server_instance = None
_server_port     = None
_server_dir      = None

_FC_ALIVE_STALE_SECONDS = 15

_KEEP = object()
def wire(*, relay_db_ref=_KEEP, focus_callback=_KEEP, refresh_callback=_KEEP,
         relay_stage_callback=_KEEP, popup_tx_callback=_KEEP, halt_tx_callback=_KEEP,
         fit_exclusion_callback=_KEEP, tx_mode_ref=_KEEP, tx_halt_ref=_KEEP,
         shared_dir_provider=_KEEP):
    """Inject the live callbacks/holders the HTTP server reads. Partial updates are
    allowed — only keywords actually passed are applied."""
    global _relay_db_ref, _focus_callback, _refresh_callback, _relay_stage_callback
    global _popup_tx_callback, _halt_tx_callback, _fit_exclusion_callback
    global _tx_mode_ref, _tx_halt_ref, _shared_dir_provider
    if relay_db_ref is not _KEEP: _relay_db_ref = relay_db_ref
    if focus_callback is not _KEEP: _focus_callback = focus_callback
    if refresh_callback is not _KEEP: _refresh_callback = refresh_callback
    if relay_stage_callback is not _KEEP: _relay_stage_callback = relay_stage_callback
    if popup_tx_callback is not _KEEP: _popup_tx_callback = popup_tx_callback
    if halt_tx_callback is not _KEEP: _halt_tx_callback = halt_tx_callback
    if fit_exclusion_callback is not _KEEP: _fit_exclusion_callback = fit_exclusion_callback
    if tx_mode_ref is not _KEEP: _tx_mode_ref = tx_mode_ref
    if tx_halt_ref is not _KEEP: _tx_halt_ref = tx_halt_ref
    if shared_dir_provider is not _KEEP: _shared_dir_provider = shared_dir_provider

def _ensure_server(directory: str):
    global _server_instance, _server_port, _server_dir
    if _server_instance is not None:
        return
    _server_dir  = directory
    _server_port = _free_port()

    class _Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=_server_dir, **kw)
        def log_message(self, *_):
            pass
        def do_GET(self):
            if self.path == '/focus':
                if _focus_callback:
                    try: _focus_callback()
                    except Exception: pass
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(b'ok')
                return
            if self.path == '/refresh':
                if _refresh_callback:
                    try: _refresh_callback()
                    except Exception: pass
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(b'ok')
                return
            if self.path.startswith('/data.json'):
                body = _data_json_bytes[0]
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', len(body))
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith('/relay.json'):
                data = {}
                try:
                    if _relay_db_ref:
                        my_call = _relay_db_ref._my_call or ''
                        for via, target, snr, logged_ts in _relay_db_ref.get_all():
                            if my_call and via.upper() == my_call.upper():
                                continue
                            data.setdefault(target, []).append({
                                'via':        via,
                                'snr':        snr,
                                'snr_to_me':  None,
                                'composite':  snr,
                                'age_min':    _relay_age_min(logged_ts),
                                'confidence': _relay_confidence(logged_ts),
                            })
                        for tgt in data:
                            data[tgt].sort(key=lambda r:
                                -(r['composite'] if r['composite'] is not None else -99))
                except Exception:
                    pass
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', len(body))
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith('/relay_yes_list.json'):
                # C.18: YES path list for Switch Path panel.
                # Includes age, failed vias, and whether target offset is known.
                _committed = _relay_committed_ref[0] or {}
                _tgt_call  = _committed.get('target', '')
                _tgt_off   = _last_seen_offset.get(_tgt_call, 0)
                _age_s     = (time.time() - _relay_yes_ts_ref[0]) if _relay_yes_ts_ref[0] else 0
                _payload   = {
                    'yes_list':    _relay_yes_list_ref[0],
                    'age_s':       int(_age_s),
                    'stale':       _age_s > 300,
                    'failed_vias': _relay_failed_vias_ref[0],
                    'target':      _tgt_call,
                    'target_offset': _tgt_off,
                }
                body = json.dumps(_payload, separators=(',', ':')).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', len(body))
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith('/relay_anim.json'):
                body = json.dumps({
                    'anim':    _relay_anim_ref[0],
                    'outcome': _relay_outcome_ref[0],
                }, separators=(',', ':')).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', len(body))
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith('/fastchat_alive'):
                # Phase 10: FastChat presence check for the callsign-popup
                # FastChat button. FastChat writes js8fastchat_alive.json into
                # the SHARED folder on startup, refreshes its "ts" every ~5 s,
                # and deletes it on clean exit. We read it here (READ ONLY --
                # JS8Map never writes or deletes this file) and report whether
                # FastChat is live so the map page can enable/gray the button.
                #
                # "alive" is True only if the file exists AND its ts is within
                # _FC_ALIVE_STALE_SECONDS (guards against a crash that left a
                # stale file behind). A future ts (clock skew) also reads dead.
                _alive = False
                try:
                    _shared = _shared_dir_provider()
                    if _shared:
                        _ap = os.path.join(_shared, 'js8fastchat_alive.json')
                        with open(_ap, 'r', encoding='utf-8') as _af:
                            _adata = json.load(_af)
                        _age = time.time() - float(_adata.get('ts', 0))
                        _alive = (0 <= _age <= _FC_ALIVE_STALE_SECONDS)
                except Exception:
                    _alive = False
                body = json.dumps({'alive': _alive}, separators=(',', ':')).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', len(body))
                self.send_header('Cache-Control', 'no-cache')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.end_headers()

        def do_POST(self):
            length = int(self.headers.get('Content-Length', 0))
            try:
                body = json.loads(self.rfile.read(length) or b'{}')
            except Exception:
                body = {}
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            if self.path == '/relay_active':
                _relay_active_ref[0] = bool(body.get('active', False))
            if self.path == '/popup_tx':
                # Callsign-popup buttons (SNR? / HEARING? / MSG).
                #   mode='snr'     → '<CALL> SNR?'      via tx_query (mode-aware)
                #   mode='hearing' → '<CALL> HEARING?'  via tx_query (mode-aware)
                #   mode='msg'     → '<CALL> MSG <text>' via tx_stage (ALWAYS
                #                    fills box, never transmits — operator sends)
                _call = ''.join(ch for ch in str(body.get('call', ''))
                                if ch.isalnum() or ch == '/').upper()
                _pmode = str(body.get('mode', '')).strip().lower()
                _ptext = str(body.get('text', '')).strip()
                _tx_mode_now = _tx_mode_ref[0]
                _effect = 'none'
                if _call and len(_call) >= 3 and any(c.isdigit() for c in _call.split('/')[0]):
                    if _pmode == 'snr' and _popup_tx_callback:
                        _popup_tx_callback(_call + ' SNR?', 'popup-snr', False)
                        _effect = ('sent' if _tx_mode_now == 'live'
                                   else 'staged' if _tx_mode_now == 'manual' else 'shadow')
                    elif _pmode == 'hearing' and _popup_tx_callback:
                        _popup_tx_callback(_call + ' HEARING?', 'popup-hearing', False)
                        _effect = ('sent' if _tx_mode_now == 'live'
                                   else 'staged' if _tx_mode_now == 'manual' else 'shadow')
                    elif _pmode == 'msg' and _popup_tx_callback:
                        # MSG (quick path) stages (fills box) regardless of TX mode.
                        _frame = (_call + ' MSG ' + _ptext).rstrip()
                        if not _ptext:
                            _frame = _call + ' MSG '   # trailing space, cursor ready
                        _popup_tx_callback(_frame, 'popup-msg', True)
                        _effect = 'staged'
                    elif _pmode == 'msg_send' and _popup_tx_callback and _ptext:
                        # Compose-window Send: the compose window IS the review
                        # step, so this TRANSMITS via tx_query (mode-aware):
                        # live=transmit, manual=fill box, shadow=log.
                        # Use the EXACT frame the compose window built (body['frame'])
                        # so relay routing 'VIA>TARGET <msg>' is preserved. Only
                        # fall back to reconstruction if no frame was supplied.
                        _frame = str(body.get('frame', '')).strip()
                        if not _frame:
                            _frame = (_call + ' MSG ' + _ptext).rstrip()
                        _popup_tx_callback(_frame, 'popup-msg-send', False)
                        _effect = ('sent' if _tx_mode_now == 'live'
                                   else 'staged' if _tx_mode_now == 'manual' else 'shadow')
                else:
                    _effect = 'badcall'
                self.wfile.write(json.dumps(
                    {'mode': _tx_mode_now, 'effect': _effect}).encode())
                return
            if self.path == '/halt_tx':
                # HALT button — send RIG.TX_HALT over the existing connection.
                # Gated by _TX_HALT_ENABLED (config); no-op + clear report if off.
                if not _tx_halt_ref[0]:
                    self.wfile.write(json.dumps({'halted': False, 'reason': 'disabled'}).encode())
                    return
                _ok = bool(_halt_tx_callback()) if _halt_tx_callback else False
                self.wfile.write(json.dumps(
                    {'halted': _ok, 'reason': '' if _ok else 'not-connected'}).encode())
                return
            if self.path == '/fastchat_handoff':
                # JS8Map -> JS8FastChat handoff (Phase 4 of the FastChat
                # integration). Writes a small intent file into _DATA_DIR --
                # the same per-user folder JS8Map writes js8_spots.db into, and
                # exactly where JS8FastChat polls for it. JS8Map is the sole
                # writer of this file; FastChat only reads it.
                #
                # File: <_DATA_DIR>/js8fastchat_intent.json
                # Body: {"callsign": "KO4BIA", "timestamp": "<ISO 8601 UTC>"}
                # Written UTF-8 WITHOUT BOM (FastChat tolerates a BOM but
                # prefers none), atomically (temp file + os.replace).
                _call = ''.join(ch for ch in str(body.get('call', ''))
                                if ch.isalnum() or ch == '/').upper()
                if not _call or len(_call) < 3:
                    self.wfile.write(json.dumps({'ok': False, 'reason': 'badcall'}).encode())
                    return
                try:
                    _ts = datetime.now(_dt_timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                    _intent = {'callsign': _call, 'timestamp': _ts}
                    # v1.6: was _DATA_DIR. FastChat finds this file via
                    # intent_poller._search_dirs() -- NOT via constants.INTENT_PATH,
                    # which is DEAD and read by nothing. The poller searches the
                    # shared mailbox, the per-user mailbox, and the legacy
                    # %LOCALAPPDATA%\JS8Map folders, and takes the NEWEST copy.
                    # WHY IT MOVED: in SCRIPT mode _DATA_DIR is the JS8Map source
                    # folder, which the poller does not search -- so the handoff
                    # only ever worked from the frozen .exe (where _DATA_DIR is
                    # %LOCALAPPDATA%\JS8Map, which it does search). SHARED_DIR
                    # works in both modes and in the installed build.
                    _intent_path = os.path.join(_shared_dir_provider() or _DATA_DIR,
                                                'js8fastchat_intent.json')
                    _tmp_path = _intent_path + '.tmp'
                    with open(_tmp_path, 'w', encoding='utf-8') as _f:
                        json.dump(_intent, _f)
                    os.replace(_tmp_path, _intent_path)
                    self.wfile.write(json.dumps({'ok': True, 'call': _call, 'timestamp': _ts}).encode())
                except Exception as _e:
                    self.wfile.write(json.dumps({'ok': False, 'reason': str(_e)}).encode())
                return
            if self.path == '/raise_window':
                # App-switch button (JS8FastChat -> JS8Map). FastChat POSTs here
                # to bring JS8Map's OWN tkinter window to the front. Reuses the
                # same proven raise sequence as the Ctrl+Shift+J hotkey and the
                # /focus endpoint: deiconify + lift + focus_force, marshalled to
                # the GUI thread via root.after(0, ...) inside _focus_callback.
                # No body needed; this carries no data, it's just a nudge.
                if _focus_callback:
                    try: _focus_callback()
                    except Exception: pass
                self.wfile.write(json.dumps({'ok': True}).encode())
                return
            if self.path == '/raise_window_fastchat':
                # App-switch button (JS8Map -> JS8FastChat). The map page POSTs
                # here (relative URL, no port needed this direction). We write a
                # small "raise my window" signal file into the SHARED MAILBOX --
                # _shared_dir_provider(), the folder both apps resolve independently.
                # FastChat's IntentPoller finds it via intent_poller._search_dirs()
                # within ~2 s and brings its MAIN console window to the front.
                #
                # File: <_shared_dir_provider()>/js8map_raise_fastchat.json
                # Body: {"timestamp": "<ISO 8601 UTC>"}   (NO callsign -- that's
                #       the whole point of a separate file from the intent one)
                # Written UTF-8 WITHOUT BOM, atomically (temp file + os.replace).
                # JS8Map is the SOLE writer; FastChat only reads.
                try:
                    _ts = datetime.now(_dt_timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                    _raise = {'timestamp': _ts}
                    # v1.6: was _DATA_DIR. This is the file behind the "switch to
                    # FastChat" button. FastChat finds it via the poller's search,
                    # NOT via constants.RAISE_PATH -- that constant is DEAD.
                    _raise_path = os.path.join(_shared_dir_provider() or _DATA_DIR,
                                               'js8map_raise_fastchat.json')
                    _tmp_path = _raise_path + '.tmp'
                    with open(_tmp_path, 'w', encoding='utf-8') as _f:
                        json.dump(_raise, _f)
                    os.replace(_tmp_path, _raise_path)
                    self.wfile.write(json.dumps({'ok': True, 'timestamp': _ts}).encode())
                except Exception as _e:
                    self.wfile.write(json.dumps({'ok': False, 'reason': str(_e)}).encode())
                return
            if self.path == '/relay_commit':
                _via = str(body.get('via',    '')).strip().upper()
                _tgt = str(body.get('target', '')).strip().upper()
                _msg = str(body.get('msg',    '')).strip()
                if _via and _tgt:
                    _relay_committed_ref[0] = {'via': _via, 'target': _tgt}
                    _relay_anim_ref[0]      = None
                    _relay_msg_ref[0]       = _msg
                    _relay_failed_vias_ref[0] = []
                    _relay_outcome_ref[0]   = None  # clear old outcome on new relay
                    # Cancel any stale timers from a previous attempt so they
                    # can't fire a spurious timeout during this fresh relay.
                    for _tmr in (_relay_watchdog_timer, _relay_dest_timer,
                                 _relay_return_timer):
                        if _tmr[0]:
                            _tmr[0].cancel()
                            _tmr[0] = None
                    # NOTE (v1.82_B): we deliberately do NOT pre-stage the relay
                    # prefix into JS8Call's box here anymore. The compose window
                    # now delivers the COMPLETE frame ('VIA>TARGET <msg>') on
                    # Send. Pre-staging just the prefix via TX.SET_TEXT left it
                    # sitting in JS8Call's outgoing box; when the compose Send
                    # then fired TX.SEND_MESSAGE with the full frame, JS8Call
                    # would not cleanly replace-and-send over the staged text, so
                    # the message stuck in the box and only flushed after a manual
                    # Halt reset JS8Call's TX state. Leaving the box untouched on
                    # commit lets the compose Send transmit cleanly. The operator
                    # still sees the prefix in the compose window's header label.
                    # (Manual/Shadow modes are unaffected: the compose Send's
                    #  TX.SET_TEXT writes the full frame to a clean box.)
            elif self.path == '/relay_outcome_clear':
                _relay_outcome_ref[0] = None
            elif self.path == '/relay_arm_clear':
                _armed_relay_ref[0]     = None
                _armed_relay_pos_ref[0] = None
                _relay_committed_ref[0] = None
                _relay_anim_ref[0]      = None
                if _relay_tx_timer[0]:
                    _relay_tx_timer[0].cancel()
                    _relay_tx_timer[0] = None
                if _relay_watchdog_timer[0]:
                    _relay_watchdog_timer[0].cancel()
                    _relay_watchdog_timer[0] = None
                if _relay_dest_timer[0]:       # C.18
                    _relay_dest_timer[0].cancel()
                    _relay_dest_timer[0] = None
                if _relay_return_timer[0]:     # return-leg watchdog
                    _relay_return_timer[0].cancel()
                    _relay_return_timer[0] = None
                _relay_tx_frame_count[0]   = 0
                _relay_tx_last_frame_ts[0] = 0.0
                _relay_yes_list_ref[0]     = []
                _relay_yes_ts_ref[0]       = 0.0
                _relay_msg_ref[0]          = ''
                _relay_failed_vias_ref[0]  = []
                _relay_via_last_frag_ts[0] = 0.0   # C.18 timer fix
                try:
                    d = json.loads(_data_json_bytes[0])
                    d['armed_target']     = None
                    d['armed_target_pos'] = None
                    d['relay_committed']  = None
                    _data_json_bytes[0] = json.dumps(d, separators=(',', ':')).encode()
                except Exception:
                    pass
            elif self.path == '/relay_switch_path':
                # C.18: operator picked a new via from Switch Path panel.
                # Mark old via as failed, reset anim state, return new clipboard prefix.
                _new_via = str(body.get('via', '')).strip().upper()
                _committed = _relay_committed_ref[0]
                if _new_via and _committed:
                    _old_via = _committed.get('via', '')
                    _tgt     = _committed.get('target', '')
                    if _old_via and _old_via not in _relay_failed_vias_ref[0]:
                        _relay_failed_vias_ref[0].append(_old_via)
                    _relay_committed_ref[0] = {'via': _new_via, 'target': _tgt}
                    _relay_anim_ref[0]      = None
                    _relay_outcome_ref[0]   = None   # clear failed outcome so pill dismisses
                    if _relay_dest_timer[0]:
                        _relay_dest_timer[0].cancel()
                        _relay_dest_timer[0] = None
                    if _relay_watchdog_timer[0]:
                        _relay_watchdog_timer[0].cancel()
                        _relay_watchdog_timer[0] = None
                    if _relay_return_timer[0]:
                        _relay_return_timer[0].cancel()
                        _relay_return_timer[0] = None
                    # Re-route STAGING (RF-safe): stage the NEW via's relay
                    # prefix into JS8Call's outgoing box via TX.SET_TEXT. Never
                    # transmits — operator types the message and sends manually.
                    if _relay_stage_callback:
                        _m = _relay_msg_ref[0]
                        _txt = (_new_via + '>' + _tgt + ' ' + (_m or '')).rstrip() + ' '
                        try: _relay_stage_callback(_txt, 'relay-switch-path')
                        except Exception: pass
                    # Return new clipboard prefix — operator types message after paste
                    _clip = f'{_new_via}>{_tgt} '
                    self.wfile.write(json.dumps({
                        'ok': True, 'via': _new_via,
                        'target': _tgt, 'clipboard': _clip,
                    }).encode())
                    return
                self.wfile.write(json.dumps({'ok': False}).encode())
                return
            elif self.path == '/set_fit_exclusion':
                call = norm_call(body.get('call'))
                excluded = bool(body.get('excluded', False))
                if call and _fit_exclusion_callback:
                    try: _fit_exclusion_callback(call, excluded)
                    except Exception: pass
            self.wfile.write(b'ok')

    _server_instance = http.server.HTTPServer(('127.0.0.1', _server_port), _Handler)
    threading.Thread(target=_server_instance.serve_forever, daemon=True).start()

    # Port discovery for the app-switch button (JS8FastChat -> JS8Map).
    # JS8Map binds a RANDOM free port (_free_port), so a separate process like
    # FastChat can't guess it. Publish the chosen port into _DATA_DIR -- the
    # same per-user folder FastChat already polls for the intent/raise files --
    # so FastChat can read it and POST to /raise_window. Rewritten on every
    # launch, so a restart on a new port is picked up by reading fresh.
    #
    # File: <_DATA_DIR>/js8map_server.json
    # Body: {"port": <_server_port>, "timestamp": "<ISO 8601 UTC>"}
    # Written UTF-8 WITHOUT BOM, atomically (temp file + os.replace).
    # JS8Map is the SOLE writer; FastChat only reads.
    try:
        _srv_ts = datetime.now(_dt_timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        _srv_info = {'port': _server_port, 'timestamp': _srv_ts}
        # v1.6: was _DATA_DIR. FastChat finds this file via
        # intent_poller.find_server_file() to learn the random port to POST
        # /raise_window to -- NOT via constants.SERVER_PATH, which is DEAD.
        # Written to JS8Map's own data folder it was invisible to FastChat in
        # SCRIPT mode, and the FastChat -> JS8Map button could never find a port.
        # (This is the file the operator photographed sitting in "JS8Map Ver 1.5\"
        # on 2026-07-12 -- the proof.)
        # NOTE: a NEW random port is chosen every launch, so this file must be
        # rewritten on every launch and read fresh on every click. Never cache it.
        _srv_path = os.path.join(_shared_dir_provider() or _DATA_DIR,
                                 'js8map_server.json')
        _srv_tmp = _srv_path + '.tmp'
        with open(_srv_tmp, 'w', encoding='utf-8') as _f:
            json.dump(_srv_info, _f)
        os.replace(_srv_tmp, _srv_path)
    except Exception:
        # Non-fatal: the map still works; only the FastChat->JS8Map button
        # loses its target until the next successful write.
        pass

def open_in_browser(filepath: str) -> str:
    """Open the map in a DEDICATED browser window (Chromium --app mode) so it
    behaves like a standalone app you can flip to from JS8FastChat: no tab
    clutter, easy to bring to the front. If that window is already open it is
    focused rather than re-opened (no duplicate windows). Falls back to a normal
    default-browser tab if no Chromium browser (Brave/Chrome/Edge) is found.
    Returns the URL.

    All browser interaction runs on a background thread so this NEVER blocks the
    GUI/caller thread (a slow browser launch must not freeze map generation)."""
    directory = os.path.dirname(os.path.abspath(filepath))
    filename  = os.path.basename(filepath)
    _ensure_server(directory)
    url = f'http://127.0.0.1:{_server_port}/{filename}'

    def _do():
        # Already open? Just bring it forward (covers manual re-Generate too).
        if _focus_existing_map_window():
            return
        # Not open -> launch a dedicated app-mode window; else default browser.
        if _launch_map_window(url, _DATA_DIR):
            return
        try:
            os.startfile(url)          # Windows: ShellExecute → default browser tab
        except Exception:
            try:
                webbrowser.open(url)   # fallback for non-Windows
            except Exception:
                pass

    try:
        import threading as _thr
        _thr.Thread(target=_do, daemon=True).start()
    except Exception:
        _do()  # if threading is somehow unavailable, do it inline
    return url
