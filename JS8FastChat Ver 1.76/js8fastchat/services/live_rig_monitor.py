from __future__ import annotations

import json
import re
import socket
import threading
import time
from typing import Callable

from ..utils import safe_int


class JS8LiveRigMonitor:
    """Persistent listener for live JS8Call rig/status fields."""

    def __init__(self, host_getter: Callable[[], str], port_getter: Callable[[], int | str], on_status, on_error):
        self.host_getter = host_getter
        self.port_getter = port_getter
        self.on_status = on_status
        self.on_error = on_error
        self._running = False
        self._thread = None
        self._sock = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="JS8LiveRigMonitor")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass

    def _send(self, msg_type: str) -> None:
        try:
            if self._sock:
                payload = json.dumps({"type": msg_type, "value": "", "params": {}}) + "\n"
                self._sock.sendall(payload.encode("utf-8"))
        except Exception:
            pass

    @staticmethod
    def _param_deep(obj, *names):
        wanted = {str(n).upper().replace(" ", "_").replace("-", "_") for n in names}

        def norm(k):
            return str(k).upper().replace(" ", "_").replace("-", "_")

        def walk(x):
            if isinstance(x, dict):
                for k, v in x.items():
                    if norm(k) in wanted:
                        return v
                for v in x.values():
                    found = walk(v)
                    if found not in (None, ""):
                        return found
            elif isinstance(x, (list, tuple)):
                for v in x:
                    found = walk(v)
                    if found not in (None, ""):
                        return found
            return None

        return walk(obj)

    @staticmethod
    def _value_as_dict(value):
        raw = str(value or "").strip()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _parse_offset_from_text(value):
        m = re.search(r"(?:OFFSET|TX[_ ]?OFFSET|AUDIO[_ ]?OFFSET|SELECTED[_ ]?OFFSET)\D*(-?\d+)", str(value or ""), re.I)
        return m.group(1) if m else None

    @staticmethod
    def _to_float(v):
        try:
            return float(str(v).strip())
        except Exception:
            return None

    def _extract_live_rig_fields(self, params, value=""):
        value_obj = self._value_as_dict(value)
        search_obj = {"params": params if isinstance(params, dict) else {}, "value": value_obj}
        dial = self._param_deep(search_obj, "DIAL", "DIAL_FREQ", "DIAL_HZ")
        actual = self._param_deep(search_obj, "FREQ", "FREQUENCY", "RIG_FREQ", "TX_FREQ")
        offset = self._param_deep(search_obj, "OFFSET", "TX_OFFSET", "AUDIO_OFFSET", "SELECTED_OFFSET", "TXOFFSET", "AUDIOOFFSET", "JS8_OFFSET", "JS8CALL_OFFSET")
        if offset in (None, ""):
            offset = self._parse_offset_from_text(value)
        if offset in (None, "") and dial not in (None, "") and actual not in (None, ""):
            d = self._to_float(dial)
            a = self._to_float(actual)
            if d is not None and a is not None:
                offset = str(int(round(a - d)))
        display_freq = dial if dial not in (None, "") else actual
        if display_freq in (None, "") and value and not value_obj:
            display_freq = value
        return display_freq, offset

    def _run(self) -> None:
        while self._running:
            host = str(self.host_getter() or "127.0.0.1").strip()
            port = safe_int(self.port_getter(), 2442)
            try:
                with socket.create_connection((host, port), timeout=4.0) as sock:
                    self._sock = sock
                    sock.settimeout(1.0)
                    self.on_status({"connected": True})
                    last_poll = 0.0
                    buf = ""
                    while self._running:
                        now = time.time()
                        if now - last_poll >= 2.0:
                            last_poll = now
                            for msg_type in ("RIG.GET_FREQ", "STATION.GET_STATUS", "STATION.GET_INFO", "STATION.GET_CALLSIGN", "MODE.GET_SPEED"):
                                self._send(msg_type)
                        try:
                            chunk = sock.recv(8192).decode("utf-8", errors="replace")
                            if not chunk:
                                break
                            buf += chunk
                            while "\n" in buf:
                                line, buf = buf.split("\n", 1)
                                line = line.strip("\r ")
                                if line:
                                    self._handle_line(line)
                        except socket.timeout:
                            continue
                        except Exception:
                            break
            except Exception as e:
                self.on_error(f"{type(e).__name__}: {e}")
                time.sleep(3)
            finally:
                self._sock = None

    def _handle_line(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except Exception:
            return
        typ = str(msg.get("type", ""))
        typ_u = typ.upper()
        params = msg.get("params", {}) or {}
        value = str(msg.get("value", "") or "")

        # ── Live long-message preview ────────────────────────────────────────
        # FastChat's own connection already receives JS8Call's broadcast events
        # (it handles TX.FRAME below). RX.ACTIVITY is the per-frame decode event
        # for a message still being received; RX.DIRECTED is its completion. Feed
        # both to the in-process assembler so the QSO popup can show a live
        # "receiving…" line. Best-effort; never disturbs rig/status handling.
        if typ_u == "RX.ACTIVITY":
            try:
                from .rx_partial import rx_partials
                rx_partials.note_fragment(params.get("OFFSET", 0),
                                          params.get("TEXT", "") or value)
            except Exception:
                pass
            return  # not a rig field
        if typ_u == "RX.DIRECTED":
            try:
                from .rx_partial import rx_partials
                rx_partials.complete(params.get("FROM", ""), params.get("OFFSET", 0))
            except Exception:
                pass
            return  # not a rig field

        data = {"connected": True, "type": typ, "freq": None, "offset": None, "callsign": None, "speed": None}
        is_live_rig_message = typ_u in ("RIG.FREQ", "STATION.STATUS", "STATION.INFO", "RIG.STATUS", "RIG.INFO") or (typ_u.startswith("RIG.") and typ_u not in ("RIG.PTT",))
        if typ_u == "TX.FRAME":
            # Broadcast notification of a frame being transmitted by ANY API
            # client (FastChat, JS8Map, or JS8Call itself) — not a request we
            # made. Pass the raw message through untouched; the caller decides
            # how (or whether) to parse/log it. Do NOT guess field names here.
            data["tx_frame_raw"] = msg
            self.on_status(data)
            return
        if is_live_rig_message:
            data["freq"], data["offset"] = self._extract_live_rig_fields(params, value)
            if typ_u in ("STATION.INFO", "STATION.STATUS"):
                data["callsign"] = value or self._param_deep(params, "CALLSIGN", "CALL")
        elif typ_u == "STATION.CALLSIGN":
            data["callsign"] = value or self._param_deep(params, "CALLSIGN", "CALL")
        elif typ_u in ("MODE.SPEED", "MODE.GET_SPEED", "MODE.STATUS"):
            value_obj = self._value_as_dict(value)
            search_obj = {"params": params if isinstance(params, dict) else {}, "value": value_obj}
            # Prefer the numeric SPEED field specifically. Only fall back to a
            # scalar value or the mode/submode name if SPEED is absent, so the
            # mode name (e.g. "JS8") never gets mistaken for the speed.
            spd = self._param_deep(search_obj, "SPEED")
            if spd in (None, "") and value and not value_obj:
                spd = value
            if spd in (None, ""):
                spd = self._param_deep(search_obj, "MODE", "SUBMODE")
            data["speed"] = spd
        if any(data.get(k) for k in ("freq", "offset", "callsign", "speed")) or data.get("connected"):
            self.on_status(data)
