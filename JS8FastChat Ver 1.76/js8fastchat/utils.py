from __future__ import annotations

import json
import re
import traceback
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence

from .constants import DEBUG_LOG, PROTOCOL_WORDS

CALLSIGN_RE = re.compile(r"^[A-Z0-9]{1,3}[0-9][A-Z]{1,4}(?:/[A-Z0-9]+)?$")
GRID_RE = re.compile(r"\b([A-R]{2}[0-9]{2}(?:[A-X]{2})?)\b", re.I)


_LAST_DEBUG_MESSAGE = [""]
_MAX_DEBUG_LOG_BYTES = 4 * 1024 * 1024


def _trim_debug_log_if_needed() -> None:
    """Keep field-test debug logs from growing without bound."""
    try:
        if DEBUG_LOG.exists() and DEBUG_LOG.stat().st_size > _MAX_DEBUG_LOG_BYTES:
            backup = DEBUG_LOG.with_suffix(DEBUG_LOG.suffix + ".old")
            try:
                if backup.exists():
                    backup.unlink()
            except Exception:
                pass
            try:
                DEBUG_LOG.replace(backup)
            except Exception:
                DEBUG_LOG.write_text("", encoding="utf-8")
    except Exception:
        pass


def debug(message: str) -> None:
    """Append one labeled diagnostic line, suppressing identical repeats."""
    try:
        text = str(message or "")
        if text == _LAST_DEBUG_MESSAGE[0]:
            return
        _LAST_DEBUG_MESSAGE[0] = text
        DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
        _trim_debug_log_if_needed()
        with DEBUG_LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {text}\n")
    except Exception:
        pass


def debug_exc(label: str) -> None:
    debug(label + ":\n" + traceback.format_exc())


def read_json(path: Path) -> dict:
    try:
        if path and path.exists():
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception:
        debug_exc(f"read_json failed for {path}")
    return {}


def write_json(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data or {}, f, indent=2)
    except Exception:
        debug_exc(f"write_json failed for {path}")


def first_existing(paths: Iterable[Path | str]) -> str:
    for raw in paths:
        if not raw:
            continue
        try:
            p = Path(raw).expanduser()
            if p.exists():
                return str(p)
        except Exception:
            pass
    return ""


CALL_DISPLAY_PREFIX_RE = re.compile(r"^(?:[\s★☆☎⚑⚠✉*]+|WATCH\s+)+", re.I)


def clean_call_display(call: object) -> str:
    """Strip UI-only symbols from displayed callsigns before selection/TX/lookup."""
    text = str(call or "").upper().strip()
    text = CALL_DISPLAY_PREFIX_RE.sub("", text).strip()
    text = text.rstrip("*").strip()
    return text


def norm_call(call: object) -> str:
    return clean_call_display(call)


def base_call(call: object) -> str:
    text = clean_call_display(call)
    return text.split("/")[0]


def is_protocol_word(text: object) -> bool:
    t = base_call(text)
    return not t or t in PROTOCOL_WORDS or t.startswith("@")


def looks_like_callsign(text: object) -> bool:
    b = base_call(text)
    return bool(b and b not in PROTOCOL_WORDS and CALLSIGN_RE.match(b))


def group_tag_name(group: str) -> str:
    return "group_" + re.sub(r"[^A-Z0-9]+", "_", norm_call(group)).strip("_")


def display_person_name(name: object) -> str:
    raw = str(name or "").strip()
    if not raw:
        return ""
    raw = re.sub(r"\s+", " ", raw)
    if "," in raw:
        last, rest = raw.split(",", 1)
        raw = " ".join(part.strip() for part in (rest, last) if part.strip())
    return raw.title()


def parse_ts(value: object) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()[:19].replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            pass
    return None


def fmt_age(ts: object, reference: Optional[datetime] = None) -> str:
    dt = parse_ts(ts)
    if not dt:
        return ""
    ref = reference or datetime.now()
    seconds = max(0, int((ref - dt).total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def fmt_freq(freq: object) -> str:
    try:
        f = float(freq or 0)
        if f <= 0:
            return ""
        if f > 100000:
            return f"{f / 1_000_000:.4f}"
        return f"{f:.4f}"
    except Exception:
        return ""


def safe_int(value: object, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def normalize_watch_term(term: str) -> str:
    text = str(term or "").upper().strip()
    text = re.sub(r"[^A-Z0-9+]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_watch_text(text: str) -> str:
    out = str(text or "").upper()
    out = re.sub(r"[^A-Z0-9]+", " ", out)
    out = re.sub(r"\s+", " ", out).strip()
    return f" {out} " if out else ""


def match_watch_words(text: str, watch_words: Sequence[str]) -> str:
    haystack = normalize_watch_text(text)
    if not haystack:
        return ""
    for raw in watch_words or []:
        term = str(raw or "").strip()
        if not term or term.startswith("#"):
            continue
        if "+" in term:
            parts = [normalize_watch_term(part) for part in term.split("+") if normalize_watch_term(part)]
            if parts and all(f" {part} " in haystack for part in parts):
                return " + ".join(parts)
            continue
        norm = normalize_watch_term(term)
        if norm and f" {norm} " in haystack:
            return norm
    return ""


def uppercase_text_value(value: str) -> str:
    return (value or "").upper()
