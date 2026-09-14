#!/usr/bin/env python3
"""FCC / Canadian amateur-license database + geocoding.

Extracted verbatim from JS8Map.py (behaviour unchanged; location only).
Downloads/builds the FCC (l_amat) and ISED Canada license databases,
geocodes records via GeoNames ZIP5 / ZIP3 / state-centroid tables, and
answers callsign lookups. One-way deps only (util, runtime, zip3_latlon)."""
from __future__ import annotations

import os
import sys
import time
import sqlite3

from js8map_util import status
from js8map_runtime import EXE_DIR as _EXE_DIR, APP_DIR as _APP_DIR
from zip3_latlon import ZIP3_LATLON as _ZIP3_LATLON


FCC_DB_CANDIDATES = [
    # Windows
    r'C:\FCC_DB', r'C:\hamdb', r'C:\FCC DB', r'C:\fccdb',
    r'D:\FCC_DB', r'D:\hamdb', r'D:\FCC DB', r'D:\fccdb',
    r'C:\hamdb\ham.db', r'C:\hamdb\ham',
    # Linux / macOS
    os.path.join(os.path.expanduser('~'), 'FCC_DB'),
    os.path.join(os.path.expanduser('~'), 'hamdb'),
    os.path.join(os.path.expanduser('~'), '.local', 'share', 'fcc_db'),
    os.path.join(os.path.expanduser('~'), 'fcc_db'),
    '/opt/fcc_db',
]

CLASS_MAP = {
    'E': 'Extra', 'A': 'Advanced', 'G': 'General',
    'N': 'Novice', 'T': 'Technician', 'P': 'Tech Plus',
}

_fcc_db_path_override: str = ''

def set_fcc_db_path(path: str):
    global _fcc_db_path_override
    _fcc_db_path_override = path.strip()
    _fcc_db_cache.clear()       # invalidate path cache when path changes
    _fcc_result_cache.clear()   # invalidate result cache too

def _dat_files_in(folder: str):
    """Locate the AM/EN .dat files in folder, matching the name CASE-INSENSITIVELY.

    The FCC zip contains AM.dat/EN.dat, but the extractor in
    download_and_update_fcc() upper-cases the basename before writing, so what
    actually lands on disk is AM.DAT / EN.DAT. This function used to test for
    the literal lowercase names with os.path.exists(). On Windows that matched
    anyway -- NTFS is case-insensitive -- so the bug was invisible for the
    life of the project. On Linux the filesystem is case-sensitive, nothing
    matched, build_fcc_db_from_dats() returned '' at its first line without
    emitting any status, and the wizard reported no database while two
    perfectly good 283 MB files sat in the folder. Do not reintroduce a
    hard-coded lowercase (or uppercase) name here.
    """
    am = en = None
    try:
        for entry in os.listdir(folder):
            low = entry.lower()
            if low == 'am.dat':
                am = os.path.join(folder, entry)
            elif low == 'en.dat':
                en = os.path.join(folder, entry)
    except OSError:
        return None, None
    if am and en:
        return am, en
    return None, None

def _load_zip5_latlon(status_cb=None) -> dict:
    """
    Build a ZIP5→(lat,lon) dict from GeoNames US postal code data.
    Downloads once from GeoNames (~2MB) into memory; result is cached
    in module-level _zip5_cache for the lifetime of the process.
    Falls back to ZIP3 centroids if download fails.
    """
    global _zip5_cache
    if _zip5_cache:
        return _zip5_cache
    try:
        import urllib.request, zipfile, io
        status(status_cb, "⏳  Downloading ZIP code coordinates from GeoNames…")
        url = "https://download.geonames.org/export/zip/US.zip"
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = resp.read()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            with zf.open('US.txt') as f:
                for line in f:
                    parts = line.decode('utf-8').split('\t')
                    if len(parts) < 11:
                        continue
                    z = parts[1].strip()
                    try:
                        lat = float(parts[9])
                        lon = float(parts[10])
                        _zip5_cache[z] = (round(lat,5), round(lon,5))
                    except ValueError:
                        pass
        status(status_cb, f"✓  Loaded {len(_zip5_cache):,} ZIP code coordinates")
    except Exception as e:
        status(status_cb, f"⚠  GeoNames download failed ({e}) — using ZIP3 fallback")
        # Fall back: expand ZIP3 centroids to cover all ZIP5s with same prefix
        for z3, ll in _ZIP3_LATLON.items():
            for sfx in range(100):
                _zip5_cache[f"{z3}{sfx:02d}"] = ll
    return _zip5_cache

_zip5_cache: dict = {}

def _download_with_retry(url: str, label: str, status_cb=None,
                         max_attempts: int = 3, backoff: int = 10) -> bytes:
    """Download a URL with chunked progress and retry on failure.
    Returns the raw bytes on success; raises the last exception on
    exhausted retries.  Backoff doubles each attempt (10s, 20s, 40s).

    label: short name shown in status messages ('FCC data', 'Canadian data').
    """
    import urllib.request
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            if attempt > 1:
                wait = backoff * (2 ** (attempt - 2))   # 10, 20, 40 …
                for remaining in range(wait, 0, -1):
                    status(status_cb,
                           f'\u23f3  Retry {attempt} of {max_attempts} '
                           f'in {remaining}s\u2026')
                    time.sleep(1)
            status(status_cb, f'\u2b07  Connecting to {label}\u2026')
            with urllib.request.urlopen(url, timeout=120) as resp:
                total    = int(resp.headers.get('Content-Length', 0))
                total_mb = total / 1_048_576 if total else 0
                chunks, received = [], 0
                while True:
                    chunk = resp.read(65536)   # 64 KB per read
                    if not chunk:
                        break
                    chunks.append(chunk)
                    received += len(chunk)
                    done_mb   = received / 1_048_576
                    if total_mb:
                        status(status_cb,
                               f'\u2b07  Downloading {label}\u2026 '
                               f'{done_mb:.1f} of {total_mb:.1f} MB')
                    else:
                        status(status_cb,
                               f'\u2b07  Downloading {label}\u2026 '
                               f'{done_mb:.1f} MB received')
            return b''.join(chunks)
        except Exception as e:
            last_err = e
            if attempt < max_attempts:
                status(status_cb,
                       f'\u26a0  Download interrupted \u2014 will retry '
                       f'({attempt}/{max_attempts})')
    raise last_err

def _find_canadian_builder(folder: str) -> str:
    """Locate build_canadian_db.py, or '' if it is nowhere to be found.

    THE SCRIPT DOES NOT LIVE IN THE DATA FOLDER ANY MORE. This function used to
    look only in `folder` (the FCC/Canadian DB folder, C:\\FCC_DB on Windows),
    which broke the moment JS8Map.iss:42 was changed in Bug Testing Phase 1 to
    install it to {app} instead. That change was correct and must NOT be
    reverted: a .py file sitting in a world-writable folder is arbitrary code
    any local user can REPLACE, and the next person to run it is typically an
    admin following the installer's own instructions. The .iss carries the full
    note, including the UNPROVEN flag that JS8Map.py had not been read at the
    time. It has now been read -- this was the caller it broke, and on an
    installed machine "build_canadian_db.py not found" was the visible symptom.

    Search order:
      1. _EXE_DIR  -- {app} when frozen and installed, and the folder holding
                      JS8Map.py when running from source. Covers both normally.
      2. _APP_DIR  -- _MEIPASS, in case a future spec bundles the script.
      3. folder    -- the historical location, kept so an existing install with
                      the script still sitting in C:\\FCC_DB keeps working.
    """
    seen = []
    for cand_dir in (_EXE_DIR, _APP_DIR, folder):
        if not cand_dir or cand_dir in seen:
            continue
        seen.append(cand_dir)
        cand = os.path.join(cand_dir, 'build_canadian_db.py')
        if os.path.exists(cand):
            return cand
    return ''

def update_canadian_db(folder: str, status_cb=None) -> bool:
    """Download latest Canadian amateur radio data from ISED and rebuild DB."""

    CA_URL = 'https://apc-cap.ic.gc.ca/datafiles/amateur_delim.zip'
    script = _find_canadian_builder(folder)
    if not script:
        status(status_cb,
               '✗  build_canadian_db.py not found in ' + _EXE_DIR + ' or ' + folder)
        return False
    try:
        import zipfile, io
        data = _download_with_retry(CA_URL, 'ISED Canada', status_cb)
        status(status_cb, '\U0001f4e6  Extracting Canadian data file\u2026')
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                dest = os.path.join(folder, os.path.basename(name))
                with zf.open(name) as sf, open(dest,'wb') as df:
                    df.write(sf.read())
        status(status_cb, '🔨  Rebuilding Canadian database…')
        if getattr(sys, 'frozen', False):
            # Frozen .exe — sys.executable is JS8Map.exe, not Python.
            # Run build_canadian_db.py in-process to avoid launching a
            # second app instance.
            import runpy
            saved_argv = sys.argv[:]
            saved_cwd  = os.getcwd()
            sys.argv = [script, 'amateur_delim.txt', 'ham.db']
            os.chdir(folder)
            try:
                runpy.run_path(script, run_name='__main__')
            except SystemExit as e:
                if e.code and e.code != 0:
                    status(status_cb,
                           f'✗  Canadian rebuild error: exit code {e.code}')
                    return False
            finally:
                sys.argv = saved_argv
                os.chdir(saved_cwd)
        else:
            # `import subprocess` ONLY -- do NOT re-import sys here.
            # sys is already imported at module scope. Importing it again
            # anywhere inside this function makes Python treat `sys` as a
            # local for the WHOLE function, so the `getattr(sys, 'frozen')`
            # test further up raised UnboundLocalError before any download
            # was attempted. The except-block below caught it and reported
            # the misleading 'Canadian update error', which is why this
            # failed identically in the first-run wizard and from the
            # Update Canadian DB button, while the older installed .exe
            # (frozen before this line existed) kept working.
            import subprocess
            result = subprocess.run(
                [sys.executable, script, 'amateur_delim.txt', 'ham.db'],
                cwd=folder, capture_output=True, text=True, timeout=600,
                encoding='utf-8', errors='replace')
            if result.returncode != 0:
                err_out = (result.stderr or result.stdout
                           or 'no output').strip()[:200]
                status(status_cb,
                       f'✗  Canadian rebuild error: {err_out}')
                return False
        status(status_cb, '✅  Canadian database updated successfully!')
        return True
    except Exception as e:
        status(status_cb, f'✗  Canadian update error: {e}')
        return False

def download_and_update_fcc(folder: str, status_cb=None) -> bool:
    """Download latest FCC amateur license zip, extract DAT files,
    delete old ham.db and rebuild. Returns True on success."""
    import zipfile, io

    FCC_URL = 'https://data.fcc.gov/download/pub/uls/complete/l_amat.zip'
    try:
        os.makedirs(folder, exist_ok=True)
        data = _download_with_retry(FCC_URL, 'fcc.gov', status_cb)
        status(status_cb, '\U0001f4e6  Extracting AM.dat / EN.dat\u2026')
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                base = os.path.basename(name).upper()
                if base in ('AM.DAT', 'EN.DAT'):
                    with zf.open(name) as src_f, \
                         open(os.path.join(folder, base), 'wb') as dst_f:
                        dst_f.write(src_f.read())
        # Remove old DB so build_fcc_db_from_dats forces a full rebuild
        db_path = os.path.join(folder, 'ham.db')
        if os.path.exists(db_path):
            os.remove(db_path)
        result = build_fcc_db_from_dats(folder, status_cb)
        if result:
            status(status_cb, '✅  FCC database updated successfully!')
            return True
        status(status_cb, '✗  FCC rebuild failed — check AM.dat / EN.dat')
        return False
    except Exception as e:
        status(status_cb, f'✗  FCC update error: {e}')
        return False

def build_fcc_db_from_dats(folder: str, status_cb=None) -> str:
    am_path, en_path = _dat_files_in(folder)
    if not am_path:
        # Say so. This branch used to return silently, which meant a missing
        # or unmatched pair produced no message anywhere -- the caller's last
        # status ('Extracting AM.dat / EN.dat') stayed on screen and looked
        # like the extract had hung.
        status(status_cb,
               f"\u2717  AM.dat / EN.dat not found in {folder}")
        return ''
    db_path = os.path.join(folder, 'ham.db')
    if os.path.exists(db_path):
        db_mtime = os.path.getmtime(db_path)
        if db_mtime >= max(os.path.getmtime(am_path), os.path.getmtime(en_path)):
            # If lat/lon columns are missing from older DB, add them silently
            # without triggering a full rebuild + GeoNames download.
            # Coordinates will be populated lazily via fcc_fallback_grid().
            try:
                conn = sqlite3.connect(db_path)
                cols = [r[1] for r in conn.execute(
                    "PRAGMA table_info(callsigns)").fetchall()]
                if 'lat' not in cols:
                    conn.execute("ALTER TABLE callsigns ADD COLUMN lat REAL")
                if 'lon' not in cols:
                    conn.execute("ALTER TABLE callsigns ADD COLUMN lon REAL")
                conn.commit()
                conn.close()
            except Exception:
                pass
            return db_path
    status(status_cb, "⏳  Building FCC database from AM.dat / EN.dat…")
    import csv

    # Load ZIP5 → lat/lon mapping (GeoNames download or ZIP3 fallback)
    zip5 = _load_zip5_latlon(status_cb)

    try:
        conn = sqlite3.connect(db_path)
        cur  = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS callsigns (
                callsign TEXT PRIMARY KEY,
                name TEXT, address TEXT, city TEXT,
                state TEXT, zip TEXT, class TEXT,
                lat REAL, lon REAL
            )""")
        # Add lat/lon columns if upgrading an older DB
        existing = [r[1] for r in cur.execute("PRAGMA table_info(callsigns)").fetchall()]
        if 'lat' not in existing:
            cur.execute("ALTER TABLE callsigns ADD COLUMN lat REAL")
        if 'lon' not in existing:
            cur.execute("ALTER TABLE callsigns ADD COLUMN lon REAL")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_callsign ON callsigns(callsign)")
        conn.commit()

        status(status_cb, "⏳  Reading EN.dat…")
        entities: dict = {}
        with open(en_path, encoding='latin-1') as f:
            for row in csv.reader(f, delimiter='|'):
                if len(row) < 18:
                    continue
                entities[row[1]] = (row[7], row[15], row[16], row[17],
                                    row[18] if len(row) > 18 else '')

        status(status_cb, "⏳  Reading AM.dat + geocoding addresses…")
        batch = []
        with open(am_path, encoding='latin-1') as f:
            for row in csv.reader(f, delimiter='|'):
                if len(row) < 6:
                    continue
                uid      = row[1]
                callsign = row[4].strip().upper()
                op_class = row[5].strip().upper()
                if uid in entities:
                    name, address, city, state, zipcode = entities[uid]
                    # Geocode: ZIP5 centroid → best available lat/lon
                    z5  = str(zipcode).strip()[:5]
                    ll  = zip5.get(z5)
                    if not ll:
                        # Try state center as last resort
                        ll = _STATE_LATLON.get(str(state).strip().upper()[:2])
                    lat = round(ll[0], 5) if ll else None
                    lon = round(ll[1], 5) if ll else None
                    batch.append((callsign, name, address, city, state,
                                  zipcode, op_class, lat, lon))
                if len(batch) >= 5000:
                    cur.executemany(
                        "INSERT OR REPLACE INTO callsigns "
                        "(callsign,name,address,city,state,zip,class,lat,lon)"
                        " VALUES(?,?,?,?,?,?,?,?,?)",
                        batch)
                    conn.commit()
                    batch.clear()
        if batch:
            cur.executemany(
                "INSERT OR REPLACE INTO callsigns "
                "(callsign,name,address,city,state,zip,class,lat,lon)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                batch)
        conn.commit()
        conn.close()
        status(status_cb, f"✓  FCC database built → {db_path}")
        return db_path
    except Exception as e:
        status(status_cb, f"✗  FCC DB build failed: {e}")
        return ''

_fcc_db_cache: dict = {}   # {override_key: path}

def _find_fcc_db() -> str:
    """Locate FCC ham.db. Result is cached after first successful find.
    If previous lookup failed (cached as empty), retry — the DB may have
    been created since (e.g. by FCC Lookup or the first-run wizard)."""
    key = _fcc_db_path_override or '__auto__'
    if key in _fcc_db_cache:
        cached = _fcc_db_cache[key]
        if cached and os.path.isfile(cached):
            return cached
        # If cached as empty or stale, fall through and search again.
    p = _fcc_db_path_override
    result = ''
    if p:
        if os.path.isdir(p):
            existing = os.path.join(p, 'ham.db')
            if os.path.isfile(existing):
                result = existing
            else:
                result = build_fcc_db_from_dats(p) or ''
        elif os.path.isfile(p):
            result = p
    if not result:
        for c in FCC_DB_CANDIDATES:
            if os.path.isdir(c):
                # First check if ham.db already exists in this folder
                # (e.g. built by FCC Lookup tool, which deletes the .dat files after).
                existing = os.path.join(c, 'ham.db')
                if os.path.isfile(existing):
                    result = existing
                else:
                    am, _ = _dat_files_in(c)
                    if am:
                        result = build_fcc_db_from_dats(c) or ''
            elif os.path.isfile(c):
                result = c
            if result:
                break
    _fcc_db_cache[key] = result
    return result

_STATE_LATLON = {
    'AL':(32.8,-86.8),'AK':(64.2,-153.4),'AZ':(34.3,-111.1),'AR':(34.8,-92.2),
    'CA':(36.8,-119.4),'CO':(39.0,-105.5),'CT':(41.6,-72.7),'DE':(39.0,-75.5),
    'FL':(28.7,-82.5),'GA':(32.7,-83.4),'HI':(20.9,-157.0),'ID':(44.4,-114.6),
    'IL':(40.0,-89.2),'IN':(40.3,-86.1),'IA':(42.1,-93.5),'KS':(38.5,-98.4),
    'KY':(37.5,-85.3),'LA':(31.2,-91.8),'ME':(45.4,-69.0),'MD':(39.0,-76.8),
    'MA':(42.3,-71.8),'MI':(44.3,-85.4),'MN':(46.4,-93.1),'MS':(32.7,-89.7),
    'MO':(38.4,-92.5),'MT':(47.0,-109.6),'NE':(41.5,-99.9),'NV':(38.5,-117.1),
    'NH':(43.7,-71.6),'NJ':(40.2,-74.7),'NM':(34.3,-106.0),'NY':(42.9,-75.5),
    'NC':(35.5,-79.4),'ND':(47.5,-100.5),'OH':(40.4,-82.8),'OK':(35.6,-97.5),
    'OR':(44.1,-120.5),'PA':(40.9,-77.8),'RI':(41.7,-71.5),'SC':(33.9,-80.9),
    'SD':(44.4,-100.2),'TN':(35.9,-86.4),'TX':(31.5,-99.3),'UT':(39.4,-111.1),
    'VT':(44.1,-72.7),'VA':(37.8,-78.2),'WA':(47.4,-120.5),'WV':(38.6,-80.6),
    'WI':(44.6,-89.9),'WY':(43.0,-107.6),'DC':(38.9,-77.0),
}

def fcc_fallback_grid(fcc_record: dict) -> str:
    """Return best approximate 6-digit grid from FCC record.
    Priority: stored lat/lon (ZIP5 geocoded) → ZIP5 live lookup → ZIP3 → state center."""
    # Best: use lat/lon stored in ham.db at build time (ZIP5 accuracy ~5mi)
    lat = fcc_record.get('lat')
    lon = fcc_record.get('lon')
    # Guard: reject (0, 0) — FCC DB stores zeros for un-geocoded records.
    # lat=0 lon=0 is the Gulf of Guinea, not a valid amateur location.
    if lat is not None and lon is not None and (lat != 0 or lon != 0):
        return latlon_to_grid6(lat, lon)
    # Fallback: live ZIP5 lookup (older DB without lat/lon columns)
    zipcode = str(fcc_record.get('zip', '') or '').strip()
    if zipcode:
        z5 = zipcode[:5]
        ll = _zip5_cache.get(z5)
        if not ll:
            ll_z3 = _ZIP3_LATLON.get(z5[:3])
            ll = ll_z3
        if ll:
            return latlon_to_grid6(ll[0], ll[1])
    # Last resort: state center
    state = str(fcc_record.get('state', '')).upper().strip()[:2]
    ll = _STATE_LATLON.get(state)
    if ll:
        return latlon_to_grid6(ll[0], ll[1])
    return ''

def load_fcc_info(callsigns: set) -> dict:
    if not callsigns:
        return {}
    db_path = _find_fcc_db()
    if not db_path:
        return {}

    # ── In-memory result cache ──────────────────────────────────────
    # Keyed by (db_path, frozenset(base_callsigns)). Avoids re-querying
    # the FCC DB on every Generate Map click for the same set of stations.
    cache_key = (db_path, frozenset(c.upper().split('/')[0] for c in callsigns))
    if cache_key in _fcc_result_cache:
        # Still need to remap portables (W3BFO/P → W3BFO result)
        cached = _fcc_result_cache[cache_key]
        result: dict = {}
        for c in callsigns:
            base = c.upper().split('/')[0]
            if base in cached:
                result[c] = cached[base]
        return result

    base_map: dict = {}
    for c in callsigns:
        base = c.upper().split('/')[0]
        base_map.setdefault(base, set()).add(c)
    bases = list(base_map.keys())
    result: dict = {}
    base_result: dict = {}
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA cache_size=-4000")   # 4 MB page cache
        placeholders = ','.join('?' * len(bases))
        rows = conn.execute(
            f"SELECT callsign, name, city, state, class, zip, lat, lon FROM callsigns "
            f"WHERE callsign IN ({placeholders})", bases
        ).fetchall()
        conn.close()
        for row in rows:
            call_base = str(row[0] or '').upper()
            _nm = str(row[1] or '').strip()
            # FCC stores names as "LAST, FIRST MI" — flip to natural reading
            # order "First MI Last" for the popup (e.g. "SCHRAMM, MICHAEL W"
            # -> "Michael W Schramm"). Names without a comma pass through.
            if ',' in _nm:
                _last, _rest = _nm.split(',', 1)
                _nm = (_rest.strip() + ' ' + _last.strip()).strip()
            info = {
                'name':  _nm.title(),
                'city':  str(row[2] or '').title(),
                'state': str(row[3] or '').upper(),
                'class': CLASS_MAP.get(str(row[4] or '').upper(), str(row[4] or '')),
                'zip':   str(row[5] or '').strip()[:5],
                'lat':   row[6],   # None if not yet geocoded
                'lon':   row[7],
            }
            base_result[call_base] = info
            for orig in base_map.get(call_base, {call_base}):
                result[orig] = info
    except Exception:
        pass

    # Cache base-callsign results; cap at 2000 entries to bound memory
    if len(_fcc_result_cache) > 2000:
        _fcc_result_cache.clear()
    _fcc_result_cache[cache_key] = base_result
    return result

_fcc_result_cache: dict = {}

def latlon_to_grid6(lat: float, lon: float) -> str:
    """Convert lat/lon to 6-character Maidenhead grid square."""
    lon_adj = lon + 180.0
    lat_adj = lat + 90.0
    g  = chr(ord('A') + int(lon_adj / 20))
    g += chr(ord('A') + int(lat_adj / 10))
    g += str(int((lon_adj % 20) / 2))
    g += str(int(lat_adj % 10))
    g += chr(ord('A') + int((lon_adj % 2) / (2 / 24)))
    g += chr(ord('A') + int((lat_adj % 1) / (1 / 24)))
    return g.upper()


def get_fcc_db_path() -> str:
    """Live read of the FCC DB path override (see set_fcc_db_path)."""
    return _fcc_db_path_override

