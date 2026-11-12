from __future__ import annotations

import configparser
import os
import re
import struct
from pathlib import Path

from ..constants import JS8CALL_INI_CANDIDATES
from ..utils import CALLSIGN_RE, debug_exc


def _env_candidates() -> list[Path]:
    out = []
    for env in ("APPDATA", "LOCALAPPDATA"):
        raw = os.environ.get(env, "")
        if raw:
            out.append(Path(raw) / "JS8Call" / "JS8Call.ini")
    return out


def _ini_dirs() -> list[Path]:
    """Directories where a JS8Call config (any profile) might live."""
    dirs: list[Path] = []
    for env in ("APPDATA", "LOCALAPPDATA"):
        raw = os.environ.get(env, "")
        if raw:
            dirs.append(Path(raw) / "JS8Call")
    try:
        home = Path.home()
        dirs.append(home / "JS8Call")
        dirs.append(home / ".config" / "JS8Call")               # Linux
        dirs.append(home / "Library" / "Preferences" / "JS8Call")  # macOS
    except Exception:
        pass
    return dirs


def _looks_like_js8call_ini(path: Path) -> bool:
    """Cheap content sniff so a JS8Call profile .ini is recognized even when it
    is not named exactly 'JS8Call.ini' (multi-config names it 'JS8Call - X.ini')."""
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:6000].lower()
    except Exception:
        return False
    return ("[configuration]" in head) or ("frequencies" in head) or ("frequencyitems" in head)


def find_js8call_ini() -> str:
    # 1) explicit override (escape hatch for non-standard installs/profiles)
    override = os.environ.get("JS8CALL_INI", "").strip()
    if override:
        try:
            if Path(override).is_file():
                return override
        except Exception:
            pass
    # 2) exact standard file in standard locations (original behavior, kept first)
    for path in _env_candidates() + list(JS8CALL_INI_CANDIDATES):
        try:
            if Path(path).is_file():
                return str(path)
        except Exception:
            pass
    # 3) fallback: any *.ini in a JS8Call config dir that sniffs as a JS8Call
    #    config — handles profile filenames like "JS8Call - MyProfile.ini"
    for d in _ini_dirs():
        try:
            if not d.is_dir():
                continue
            inis = sorted(d.glob("*.ini"))
            for p in inis:
                if _looks_like_js8call_ini(p):
                    return str(p)
            if len(inis) == 1:
                return str(inis[0])
        except Exception:
            pass
    return ""


def _parser_from_file(path: str) -> configparser.RawConfigParser | None:
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
        parser = configparser.RawConfigParser(strict=False)
        parser.read_string("[__root__]\n" + raw)
        return parser
    except Exception:
        debug_exc("JS8Call.ini parse failed")
        return None


def read_js8call_highlights(ini_path: str = "") -> set[str]:
    path = ini_path or find_js8call_ini()
    if not path:
        return set()
    parser = _parser_from_file(path)
    if not parser:
        return set()
    calls: set[str] = set()
    best_score = 0
    best_calls: set[str] = set()
    for section in parser.sections():
        for key, value in parser.items(section):
            if not value or len(value) < 3:
                continue
            parts = [p.strip().upper() for p in str(value).split(",")]
            matched = {p for p in parts if CALLSIGN_RE.match(p) and len(p) >= 3}
            if not matched:
                continue
            name_score = sum(1 for word in ("secondary", "highlight", "word", "call", "watch") if word in key.lower())
            score = len(matched) + name_score * 3
            if score > best_score:
                best_score = score
                best_calls = matched
            calls.update(matched)
    return best_calls if best_calls else calls


def read_js8call_groups(ini_path: str = "") -> set[str]:
    path = ini_path or find_js8call_ini()
    if not path:
        return set()
    parser = _parser_from_file(path)
    if not parser:
        return set()
    best_score = 0
    best_groups: set[str] = set()
    all_groups: set[str] = set()
    for section in parser.sections():
        for key, value in parser.items(section):
            value = str(value or "")
            if "@" not in value:
                continue
            matched = {m.group(0).upper() for m in re.finditer(r"@[A-Z0-9_\-]{2,}", value.upper())}
            matched = {g for g in matched if g not in ("@", "@HB")}
            if not matched:
                continue
            haystack = (str(section) + " " + str(key)).lower()
            name_score = sum(1 for word in ("group", "groups", "callsign", "station", "my_groups") if word in haystack)
            score = len(matched) + name_score * 5
            if score > best_score:
                best_score = score
                best_groups = matched
            all_groups.update(matched)
    return best_groups if best_groups else all_groups


def _qt_settings_unescape(value: str) -> bytes:
    """Decode Qt/QSettings escaped @Variant(...) text into bytes.

    JS8Call stores Settings > Frequencies as Qt custom variant blobs, not as
    readable text. QSettings escapes NUL, hex bytes, and control characters in
    the INI file. This decoder handles the escape forms seen in JS8Call.ini,
    including one- or two-digit \\x escapes and short escapes like \\b.
    """
    s = str(value or "").strip()
    if s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    m = re.search(r"@Variant\((.*)\)\s*$", s)
    if m:
        s = m.group(1)
    out = bytearray()
    i = 0
    escapes = {"0": 0, "n": 10, "r": 13, "t": 9, "b": 8, "f": 12, "a": 7, "v": 11}
    while i < len(s):
        c = s[i]
        if c != "\\":
            out.append(ord(c) & 0xFF)
            i += 1
            continue
        i += 1
        if i >= len(s):
            out.append(ord("\\"))
            break
        c = s[i]
        if c in escapes:
            out.append(escapes[c])
            i += 1
        elif c == "x":
            j = i + 1
            hexchars = ""
            while j < len(s) and len(hexchars) < 2 and s[j] in "0123456789abcdefABCDEF":
                hexchars += s[j]
                j += 1
            if hexchars:
                out.append(int(hexchars, 16))
                i = j
            else:
                out.append(ord("x"))
                i += 1
        elif c in ('\\', '"'):
            out.append(ord(c))
            i += 1
        else:
            # Preserve unknown escaped characters as their literal byte. This is
            # safer than failing the whole frequency list.
            out.append(ord(c) & 0xFF)
            i += 1
    return bytes(out)


def _read_u32(buf: bytes, off: int) -> tuple[int, int]:
    if off + 4 > len(buf):
        raise ValueError("short u32")
    return struct.unpack_from(">I", buf, off)[0], off + 4


def _read_u64(buf: bytes, off: int) -> tuple[int, int]:
    if off + 8 > len(buf):
        raise ValueError("short u64")
    return struct.unpack_from(">Q", buf, off)[0], off + 8


def _read_byte_array(buf: bytes, off: int) -> tuple[str, int]:
    n, off = _read_u32(buf, off)
    if n > len(buf) - off:
        raise ValueError("short byte array")
    data = buf[off:off + n]
    off += n
    if data.endswith(b"\x00"):
        data = data[:-1]
    return data.decode("utf-8", errors="replace"), off


def _read_utf16_qstring(buf: bytes, off: int) -> tuple[str, int]:
    n, off = _read_u32(buf, off)
    if n == 0xFFFFFFFF:
        return "", off
    if n > len(buf) - off:
        raise ValueError("short QString")
    data = buf[off:off + n]
    off += n
    if not data:
        return "", off
    return data.decode("utf-16-be", errors="replace").strip(), off


# Row layout per variant name: True means each row carries a trailing UTF-16
# label (the operator's own text, e.g. "HRMS", "AmRRON"). See the docstring in
# _parse_frequency_variant for why this is a table and not a version test.
_VARIANT_HAS_LABEL = {
    "FrequencyItems_v2": False,
    "FrequencyList_v3::FrequencyItems": True,
    "FrequencyItems_v3": True,
    "FrequencyList_v2::FrequencyItems": False,
}


def _parse_frequency_variant(value: str) -> list[dict]:
    """Parse JS8Call FrequenciesForRegionModes Qt variant data.

    TWO independent things vary: the NAMING CONVENTION and the ROW LAYOUT.
    JS8Call has shipped both naming styles with either layout, so the version
    number in the name does NOT reliably indicate which:

      FrequencyItems_v2                  Hz + mode + region
      FrequencyList_v3::FrequencyItems   Hz + mode + region + UTF-16 label
      FrequencyItems_v3                  Hz + mode + region + UTF-16 label
      FrequencyList_v2::FrequencyItems   Hz + mode + region

    The last two were found in a live Linux JS8Call.ini holding 23 saved
    frequencies; because neither name was recognised the whole list was
    discarded and the Set Freq dropdown came up EMPTY. Layout is therefore
    looked up per name rather than inferred, and an unknown name still
    returns [] rather than guessing and mis-parsing the byte stream.
    """
    if "@Variant" not in str(value or ""):
        return []
    buf = _qt_settings_unescape(value)
    try:
        _variant_type, off = _read_u32(buf, 0)
        name_len, off = _read_u32(buf, off)
        if name_len <= 0 or name_len > len(buf) - off:
            return []
        name_bytes = buf[off:off + name_len]
        off += name_len
        variant_name = name_bytes.rstrip(b"\x00").decode("ascii", errors="replace")
        if variant_name not in _VARIANT_HAS_LABEL:
            return []
        count, off = _read_u32(buf, off)
        if not (0 <= count <= 500):
            return []
        rows: list[dict] = []
        for _ in range(count):
            hz, off = _read_u64(buf, off)
            mode, off = _read_byte_array(buf, off)
            region, off = _read_byte_array(buf, off)
            desc = ""
            if _VARIANT_HAS_LABEL[variant_name]:
                desc, off = _read_utf16_qstring(buf, off)
            mhz = hz / 1_000_000.0
            # Sequential parsing should prevent false positives; keep this range
            # broad enough for HF/VHF/UHF and unusual saved test entries.
            if 0.01 <= mhz <= 3000.0:
                rows.append({"hz": int(hz), "mhz": round(mhz, 6), "mode": mode.strip(), "region": region.strip(), "desc": desc.strip(), "variant": variant_name})
        return rows
    except Exception:
        debug_exc("JS8Call frequency variant parse failed")
        return []


def _band_for_mhz(mhz: float) -> str:
    if 1.7 <= mhz < 2.1:
        return "160m"
    if 3.0 <= mhz < 4.2:
        return "80m"
    if 5.0 <= mhz < 5.6:
        return "60m"
    if 7.0 <= mhz < 7.4:
        return "40m"
    if 10.0 <= mhz < 10.2:
        return "30m"
    if 14.0 <= mhz < 14.4:
        return "20m"
    if 18.0 <= mhz < 18.3:
        return "17m"
    if 21.0 <= mhz < 21.6:
        return "15m"
    if 24.8 <= mhz < 25.1:
        return "12m"
    if 28.0 <= mhz < 30.0:
        return "10m"
    if 50.0 <= mhz < 54.5:
        return "6m"
    if 144.0 <= mhz < 148.5:
        return "2m"
    return ""


def _fmt_freq_label(band: str, mhz: float, desc: str = "") -> str:
    whole = int(mhz)
    frac = int(round((mhz - whole) * 1_000_000))
    if frac >= 1_000_000:
        whole += 1
        frac = 0
    mhz_text = f"{whole}.{frac // 1000:03d} {frac % 1000:03d} MHz"
    left = f"{band}: {mhz_text}" if band else mhz_text
    return f"{left} - {desc.strip()}" if desc and desc.strip() else left


def read_js8call_frequencies(ini_path: str = "") -> list[dict]:
    """Return JS8Call Settings > Frequencies as [{label, mhz, hz, ...}].

    This intentionally does not maintain or silently inject a separate FastChat
    frequency database. The dropdown is a read-only view of JS8Call.ini. If no
    JS8Call frequency table can be decoded, an empty list is returned so the UI
    can say that no JS8Call saved frequencies were found.
    """
    path = ini_path or find_js8call_ini()
    if not path:
        return []
    found: dict[int, dict] = {}

    def add_row(row: dict, source_key: str = "") -> None:
        try:
            hz = int(row.get("hz") or round(float(row.get("mhz", 0)) * 1_000_000))
            mhz = hz / 1_000_000.0
        except Exception:
            return
        if not (0.01 <= mhz <= 3000.0):
            return
        desc = str(row.get("desc", "") or "").strip()
        mode = str(row.get("mode", "") or "").strip()
        region = str(row.get("region", "") or "").strip()
        band = _band_for_mhz(mhz)
        old = found.get(hz)
        if old:
            # Prefer the row with a useful label/description; otherwise keep the
            # first source. This lets v3 labels like HRMS/MAGNET enhance v2 rows.
            if desc and len(desc) >= len(str(old.get("desc", "") or "")):
                old.update({"desc": desc, "mode": mode or old.get("mode", ""), "region": region or old.get("region", ""), "source": source_key or old.get("source", "")})
            return
        found[hz] = {"hz": hz, "mhz": round(mhz, 6), "desc": desc, "mode": mode, "region": region, "band": band, "source": source_key}

    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
        for line in raw.splitlines():
            if "FrequenciesForRegionModes" not in line or "@Variant" not in line:
                continue
            key, value = line.split("=", 1) if "=" in line else ("", line)
            for row in _parse_frequency_variant(value):
                add_row(row, source_key=key.strip())
    except Exception:
        debug_exc("read_js8call_frequencies failed")
        return []

    out = []
    for hz, row in sorted(found.items(), key=lambda item: item[0]):
        label = _fmt_freq_label(row.get("band", ""), row.get("mhz", 0.0), row.get("desc", ""))
        item = dict(row)
        item["label"] = label
        out.append(item)
    return out[:120]
