from __future__ import annotations

import json
import re
import socket
import time
from typing import Callable, List, Tuple

from ..utils import safe_int


class JS8ApiClient:
    # Repeated-failure suppression. exchange() has no retry loop -- it is one
    # attempt per call -- but several independent callers poll it on their own
    # timers (the JS8Map lease poll at 1s, auto-refresh at 2s, speed sync at 3s,
    # popup heartbeats). When JS8Call is not running, EVERY one of those printed
    # a full ConnectionRefusedError line, so the debug log grew without bound and
    # buried anything useful. OBSERVED 2026-07-19 when JS8Call exited on its own.
    # First failure prints in full; identical repeats are counted and re-printed
    # at most once per interval; recovery prints once with the total.
    _FAIL_LOG_INTERVAL = 30.0

    def __init__(self, host_getter: Callable[[], str], port_getter: Callable[[], int | str]):
        self.host_getter = host_getter
        self.port_getter = port_getter
        self._fail_sig: str = ""
        self._fail_count: int = 0
        self._fail_last_log: float = 0.0

    def _host_port(self) -> Tuple[str, int]:
        host = str(self.host_getter() or "127.0.0.1").strip()
        port = safe_int(self.port_getter(), 2442)
        return host, port

    @staticmethod
    def _param(params: dict, *names, default=""):
        if not isinstance(params, dict):
            return default
        wanted = {str(n).upper() for n in names}
        for k, v in params.items():
            if str(k).upper() in wanted:
                return v
        return default

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

    def _note_failure(self, where: str, sig: str) -> None:
        """Log a failed exchange, collapsing identical repeats.

        Identical means same exception type, message and endpoint. A DIFFERENT
        error always prints immediately -- suppression must never hide a change
        in what is wrong.
        """
        now = time.monotonic()
        if sig == self._fail_sig:
            self._fail_count += 1
            if now - self._fail_last_log < self._FAIL_LOG_INTERVAL:
                return
            self._fail_last_log = now
            print(f"[TXDEBUG][js8_api] {where} STILL FAILING -- {sig} (x{self._fail_count}, suppressed since last line)")
            return
        self._fail_sig = sig
        self._fail_count = 1
        self._fail_last_log = now
        print(f"[TXDEBUG][js8_api] {where} FAILED -- {sig}")

    def _note_success(self, where: str) -> None:
        if self._fail_count:
            print(f"[TXDEBUG][js8_api] {where} recovered after {self._fail_count} failed attempt(s)")
        self._fail_sig = ""
        self._fail_count = 0
        self._fail_last_log = 0.0

    def exchange(self, messages: List[dict], wait_seconds: float = 1.5) -> Tuple[bool, List[dict], str]:
        host, port = self._host_port()
        replies: List[dict] = []
        try:
            with socket.create_connection((host, port), timeout=3.0) as sock:
                sock.settimeout(wait_seconds)
                for msg in messages:
                    sock.sendall((json.dumps(msg) + "\n").encode("utf-8"))
                buf = ""
                while True:
                    try:
                        chunk = sock.recv(8192).decode("utf-8", errors="replace")
                        if not chunk:
                            print(f"[TXDEBUG][js8_api] exchange({host}:{port}): recv returned empty -- connection closed by peer")
                            break
                        buf += chunk
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            line = line.strip("\r ")
                            if not line:
                                continue
                            try:
                                replies.append(json.loads(line))
                            except Exception:
                                replies.append({"raw": line})
                    except socket.timeout:
                        break
            self._note_success(f"exchange({host}:{port})")
            return True, replies, ""
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            self._note_failure(f"exchange({host}:{port})", err)
            return False, replies, err

    def _apply_rig_params(self, info: dict, params: dict, value: str = "") -> None:
        merged = {"params": params if isinstance(params, dict) else {}}
        raw_value = str(value or "").strip()
        if raw_value:
            try:
                parsed = json.loads(raw_value)
                if isinstance(parsed, dict):
                    merged["value"] = parsed
            except Exception:
                pass
        dial = self._param_deep(merged, "DIAL", "DIAL_FREQ", "DIAL_HZ")
        freq_actual = self._param_deep(merged, "FREQ", "FREQUENCY", "RIG_FREQ", "TX_FREQ")
        off = self._param_deep(merged, "OFFSET", "TX_OFFSET", "AUDIO_OFFSET", "SELECTED_OFFSET", "TXOFFSET", "AUDIOOFFSET", "JS8_OFFSET", "JS8CALL_OFFSET")
        if off in (None, "") and raw_value:
            m = re.search(r"(?:OFFSET|TX[_ ]?OFFSET|AUDIO[_ ]?OFFSET|SELECTED[_ ]?OFFSET)\D*(-?\d+)", raw_value, re.I)
            if m:
                off = m.group(1)
        if not info.get("freq") and (dial not in (None, "") or freq_actual not in (None, "") or raw_value):
            info["freq"] = dial or freq_actual or raw_value
        if off not in (None, ""):
            try:
                info["offset"] = str(int(round(float(off))))
            except Exception:
                info["offset"] = str(off)
        elif not info.get("offset") and dial not in (None, "") and freq_actual not in (None, ""):
            try:
                info["offset"] = str(int(round(float(freq_actual) - float(dial))))
            except Exception:
                pass

    def probe(self) -> Tuple[bool, dict, str]:
        messages = [
            {"type": "STATION.GET_CALLSIGN", "value": "", "params": {}},
            {"type": "STATION.GET_GRID", "value": "", "params": {}},
            {"type": "RIG.GET_FREQ", "value": "", "params": {}},
            {"type": "STATION.GET_STATUS", "value": "", "params": {}},
            {"type": "MODE.GET_SPEED", "value": "", "params": {}},
        ]
        ok, replies, err = self.exchange(messages, wait_seconds=2.0)
        info = {"callsign": "", "grid": "", "freq": "", "offset": "", "speed": ""}
        if ok:
            for msg in replies:
                typ = str(msg.get("type", ""))
                value = str(msg.get("value", "") or "")
                params = msg.get("params", {}) or {}
                if typ == "STATION.CALLSIGN":
                    info["callsign"] = value or str(self._param(params, "CALLSIGN", "CALL"))
                elif typ == "STATION.GRID":
                    info["grid"] = value or str(self._param(params, "GRID", "LOCATOR"))
                elif typ in ("RIG.FREQ", "STATION.STATUS", "STATION.INFO") or any(str(k).upper() in ("DIAL", "FREQ", "OFFSET") for k in getattr(params, "keys", lambda: [])()):
                    self._apply_rig_params(info, params, value)
                elif typ in ("MODE.SPEED", "MODE.GET_SPEED", "MODE.STATUS"):
                    info["speed"] = value or self._param(params, "SPEED", "MODE")
        return ok, info, err

    def get_config(self) -> Tuple[bool, dict, str]:
        """Fetch the running station configuration (JS8Call-improved / 3.x only).

        Sends STATION.GET_CONFIG and returns the STATION.CONFIG params dict
        (keys such as MY_GROUPS, AVOID_ALLCALL, SPEED, MONITOR, ...). Legacy
        JS8Call silently ignores the unknown command, so a legacy build yields
        ok=True with an empty dict -- the caller falls back accordingly.
        """
        ok, replies, err = self.exchange([{"type": "STATION.GET_CONFIG", "value": "", "params": {}}], wait_seconds=1.5)
        if not ok:
            return False, {}, err
        for msg in replies:
            if str(msg.get("type", "")).upper() == "STATION.CONFIG":
                params = msg.get("params", {}) or {}
                return True, (params if isinstance(params, dict) else {}), ""
        return True, {}, ""

    @staticmethod
    def _normalize_groups(raw) -> List[str]:
        """Normalize a MY_GROUPS value to @-prefixed uppercase group strings.

        JS8Call-improved returns a list (observed on 3.x); other builds could
        return a delimited string. Accept either: split strings on comma/
        semicolon/whitespace, uppercase, ensure a leading '@', drop @HB, and
        dedupe while preserving first-seen order.
        """
        if isinstance(raw, (list, tuple)):
            tokens = [str(t) for t in raw]
        else:
            tokens = re.split(r"[,;\s]+", str(raw or ""))
        out: List[str] = []
        seen = set()
        for tok in tokens:
            g = tok.strip().upper()
            if not g:
                continue
            if not g.startswith("@"):
                g = "@" + g
            if g == "@HB" or g in seen:
                continue
            seen.add(g)
            out.append(g)
        return out

    def get_my_groups(self) -> List[str]:
        """Return the operator's configured groups from JS8Call live, normalized.

        Empty list if unavailable (legacy API, JS8Call down, or no MY_GROUPS) --
        the caller then falls back to the .ini-derived list.
        """
        ok, cfg, _err = self.get_config()
        if not ok or not cfg:
            return []
        return self._normalize_groups(self._param(cfg, "MY_GROUPS"))

    def halt_tx(self) -> Tuple[bool, str]:
        ok, _replies, err = self.exchange([{"type": "RIG.TX_HALT", "value": "", "params": {}}], wait_seconds=0.8)
        return (True, "Sent RIG.TX_HALT to JS8Call.") if ok else (False, err)

    def set_speed(self, speed_code: int) -> Tuple[bool, str, List[dict]]:
        try:
            code = int(speed_code)
        except Exception:
            return False, "Invalid speed code", []
        ok, replies, err = self.exchange([{"type": "MODE.SET_SPEED", "value": "", "params": {"SPEED": code}}], wait_seconds=1.4)
        if not ok:
            return False, err, replies
        for r in replies:
            if str(r.get("type", "")).upper() == "API.ERROR":
                return False, str(r.get("value", "API.ERROR")), replies
        return True, f"Sent MODE.SET_SPEED {code}", replies

    def get_speed(self):
        return self.exchange([{"type": "MODE.GET_SPEED", "value": "", "params": {}}], wait_seconds=1.5)

    def current_speed(self):
        """Query JS8Call for the current submode speed and return it parsed.

        Uses the request/response exchange, which is reliable, unlike a passive
        listen. Returns the raw speed value (int code or name) or None.
        """
        ok, replies, err = self.get_speed()
        if not ok:
            return None
        for msg in replies:
            if str(msg.get("type", "")).upper() in ("MODE.SPEED", "MODE.GET_SPEED", "MODE.STATUS"):
                params = msg.get("params", {}) or {}
                value = str(msg.get("value", "") or "")
                spd = self._param(params, "SPEED")
                if spd in (None, ""):
                    spd = value if value else self._param(params, "MODE")
                return spd
        return None


    @staticmethod
    def _truthy_ptt(val) -> bool:
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return val != 0
        s = str(val).strip().lower()
        return s in ("1", "on", "true", "yes", "tx", "ptt")

    def tx_active(self):
        """Probe whether JS8Call is transmitting (Improved/3.x only).

        Sends RIG.GET_PTT and TX.GET_QUEUE_DEPTH and returns
        (active: bool, determined: bool). De-flicker rule for multi-frame sends:
        active = (PTT on) OR (queue depth > 0). 'determined' is False if neither
        value could be read, so the caller falls back to its TTL rather than
        releasing on a bad read.
        """
        ok, replies, err = self.exchange([
            {"type": "RIG.GET_PTT", "value": "", "params": {}},
            {"type": "TX.GET_QUEUE_DEPTH", "value": "", "params": {}},
        ], wait_seconds=1.0)
        if not ok:
            return False, False
        ptt = None
        depth = None
        for msg in replies:
            typ = str(msg.get("type", "")).upper()
            params = msg.get("params", {}) or {}
            value = msg.get("value", "")
            if typ in ("RIG.PTT", "RIG.PTT_STATUS", "RIG.GET_PTT"):
                p = self._param(params, "PTT")
                if p in (None, ""):
                    p = value
                if p not in (None, ""):
                    ptt = self._truthy_ptt(p)
            elif typ in ("TX.QUEUE_DEPTH", "TX.GET_QUEUE_DEPTH", "TX.QUEUE", "TX.DEPTH"):
                d = self._param(params, "DEPTH")
                if d in (None, ""):
                    d = value
                try:
                    depth = int(d)
                except Exception:
                    depth = None
        active = (ptt is True) or (depth is not None and depth > 0)
        determined = (ptt is not None) or (depth is not None)
        print(f"[TXDEBUG][js8_api] tx_active(): ptt={ptt!r} depth={depth!r} -> active={active} determined={determined}")
        return active, determined

    def send_message(self, text: str) -> Tuple[bool, str]:
        text = (text or "").strip().upper()
        if not text:
            return False, "Empty TX frame."
        ok, replies, err = self.exchange([{"type": "TX.SEND_MESSAGE", "value": text, "params": {"TEXT": text}}], wait_seconds=1.2)
        if not ok:
            return False, err
        for r in replies:
            if str(r.get("type", "")).upper() == "API.ERROR":
                return False, str(r.get("value", "API.ERROR"))
        return True, f"Sent to JS8Call: {text}"

    def store_message(self, callsign: str, text: str) -> Tuple[bool, str]:
        """Store a message LOCALLY at *our* station for `callsign`, to be handed
        over when that station later sends us QUERY MSGS / QUERY MSG [ID].

        This is JS8Call's own "store message" action (the one on its right-click
        menu) driven over the API -- it does NOT transmit anything. Contrast
        with sending an on-air "CALL MSG text" frame, which asks the REMOTE
        station to store the message instead.

        Note: JS8Call ignores unknown API commands silently rather than
        erroring, so a True here means "sent without error", not "definitely
        stored". The caller should verify by re-reading the inbox.
        """
        call = str(callsign or "").strip().upper()
        body = str(text or "").strip()
        if not call:
            return False, "No callsign for stored message."
        if not body:
            return False, "Stored message is empty."
        msg = {
            "type": "INBOX.STORE_MESSAGE",
            "value": body,
            "params": {"CALLSIGN": call, "TO": call, "TEXT": body},
        }
        ok, replies, err = self.exchange([msg], wait_seconds=1.5)
        if not ok:
            return False, err
        for r in replies:
            if str(r.get("type", "")).upper() == "API.ERROR":
                return False, str(r.get("value", "API.ERROR"))
        return True, f"Sent INBOX.STORE_MESSAGE for {call}"

    def get_inbox_messages(self) -> Tuple[bool, List[dict], str]:
        """Ask JS8Call for its stored messages. Used to confirm the API supports
        inbox commands on this JS8Call build."""
        ok, replies, err = self.exchange(
            [{"type": "INBOX.GET_MESSAGES", "value": "", "params": {}}], wait_seconds=1.5
        )
        return ok, replies, err

    def set_frequency(self, dial_text: str, offset_text: str = "") -> Tuple[bool, str]:
        raw = str(dial_text or "").strip().upper().replace("MHZ", "").strip()
        if not raw:
            return False, "Set Freq is blank."
        try:
            val = float(raw)
            dial_hz = int(round(val * 1_000_000)) if val < 100000 else int(round(val))
        except Exception:
            return False, f"Invalid frequency: {dial_text}"
        params = {"DIAL": dial_hz}
        off_raw = str(offset_text or "").strip()
        if off_raw:
            try:
                offset = int(round(float(off_raw)))
                params["OFFSET"] = offset
                params["FREQ"] = dial_hz + offset
            except Exception:
                return False, f"Invalid offset: {offset_text}"
        ok, _replies, err = self.exchange([{"type": "RIG.SET_FREQ", "value": "", "params": params}], wait_seconds=1.0)
        if ok:
            msg = f"Sent RIG.SET_FREQ DIAL={dial_hz}"
            if "OFFSET" in params:
                msg += f" OFFSET={params['OFFSET']}"
            return True, msg
        return False, err
