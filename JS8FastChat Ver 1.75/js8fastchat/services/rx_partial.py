from __future__ import annotations

import threading
import time
from typing import Optional


def _base_call(c) -> str:
    c = str(c or "").strip().upper()
    return c.split("/")[0] if "/" in c else c


class RxPartialStore:
    """In-process assembler for long INCOMING JS8 messages (live preview).

    JS8Call decodes a long directed message one ~15-second frame at a time. It
    emits an RX.ACTIVITY per frame carrying that frame's partial TEXT, and only
    fires RX.DIRECTED (the complete text) once every frame has landed. So the
    finished directed row that FastChat normally shows can be a minute or more
    behind the on-air reality.

    This store accumulates the per-frame fragments so the QSO popup can show a
    live "receiving…" line that fills in frame by frame and clears the instant
    the real message completes. It is fed directly by FastChat's existing live
    JS8Call listener (services.live_rig_monitor) -- no shared file, no JS8Map.

    Keying: RX.ACTIVITY has no FROM field, so fragments are grouped by audio
    OFFSET (with a small Hz tolerance for drift). Concurrent transmissions sit at
    different offsets and stay separate. The sender/target are recovered from the
    first frame's "CALL: TARGET …" prefix; later frames are pure content and are
    concatenated (JS8 frames cut mid-word, so a plain join reassembles them).

    Written from the listener thread, read from the Tk UI thread -- all access is
    guarded by a lock. Every method is best-effort and never raises, so a preview
    hiccup can never disturb the listener or the UI.
    """

    _OFFSET_TOL = 6      # Hz: match frames of one transmission (drift-tolerant)
    _TIMEOUT = 120.0     # s since last fragment before a stale partial is dropped
    _MIN_FRAMES = 2      # only expose genuinely multi-frame (long) messages

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._parts: dict = {}   # offset(int) -> {from,to,text,frags,first,last,offset}

    # ---- fragment parsing ---------------------------------------------------
    @staticmethod
    def _split(text):
        """('K4EXA', ' KW3KW INFO …') when text starts with a 'CALL:' sender
        prefix, else (None, text). A sender token is callsign-shaped."""
        up = str(text or "")
        head, sep, rest = up.partition(":")
        if sep:
            token = head.strip().replace("/", "")
            if token and token.replace("-", "").isalnum() and any(ch.isdigit() for ch in token):
                return _base_call(head.strip()), rest
        return None, up

    @staticmethod
    def _target(remainder):
        """First token after the sender prefix is the addressed target (callsign
        or @GROUP): 'KW3KW INFO' -> 'KW3KW', '@GHOSTNET SNR?' -> '@GHOSTNET'."""
        toks = str(remainder or "").split()
        if not toks:
            return ""
        tok = toks[0].strip().upper().rstrip("?.,!:;")
        return tok if tok.startswith("@") else _base_call(tok)

    def _prune(self, now=None) -> None:
        now = now if now is not None else time.monotonic()
        for o in [o for o, e in self._parts.items() if now - e["last"] > self._TIMEOUT]:
            self._parts.pop(o, None)

    # ---- fed by the live listener ------------------------------------------
    def note_fragment(self, offset, text) -> None:
        """Accumulate one RX.ACTIVITY frame fragment into its in-progress
        transmission (matched by offset)."""
        try:
            off = int(offset or 0)
            raw = str(text or "")
            if not raw.strip():
                return
            now = time.monotonic()
            sender, remainder = self._split(raw)
            with self._lock:
                self._prune(now)
                key = next((o for o in self._parts if abs(o - off) <= self._OFFSET_TOL), None)
                if key is None:
                    e = {"from": sender or "", "to": "", "text": raw, "frags": 1,
                         "first": now, "last": now, "offset": off}
                    if sender:
                        e["to"] = self._target(remainder)
                    self._parts[off] = e
                else:
                    e = self._parts[key]
                    if sender and not e["from"]:
                        e["from"] = sender
                        if not e["to"]:
                            e["to"] = self._target(remainder)
                    e["text"] = str(e["text"]) + raw
                    e["frags"] = int(e["frags"]) + 1
                    e["last"] = now
        except Exception:
            pass

    def complete(self, from_call="", offset=0) -> None:
        """Clear the in-progress preview for a transmission that just finished
        (its RX.DIRECTED arrived). Matches by offset first, then by sender."""
        try:
            off = int(offset or 0)
            fb = _base_call(from_call)
            with self._lock:
                drop = [o for o in self._parts if abs(o - off) <= self._OFFSET_TOL]
                if not drop and fb:
                    drop = [o for o, e in self._parts.items() if _base_call(e["from"]) == fb]
                for o in drop:
                    self._parts.pop(o, None)
        except Exception:
            pass

    # ---- read by the UI -----------------------------------------------------
    def partial_for(self, call) -> Optional[dict]:
        """The freshest in-progress preview FROM `call`, or None. Only genuinely
        multi-frame messages (>= 2 frames) are exposed, so single-frame traffic
        (SNR replies, heartbeats) never produces a preview line."""
        try:
            base = _base_call(call)
            with self._lock:
                self._prune()
                best = None
                for e in self._parts.values():
                    if e["frags"] < self._MIN_FRAMES:
                        continue
                    if _base_call(e["from"]) != base:
                        continue
                    best = e
                if not best:
                    return None
                return {"from": best["from"], "to": best["to"],
                        "text": " ".join(str(best["text"]).split()),
                        "offset": int(best["offset"]), "frags": int(best["frags"])}
        except Exception:
            return None

    def any_active(self) -> bool:
        """True if any multi-frame preview is currently in flight (diagnostic)."""
        try:
            with self._lock:
                self._prune()
                return any(e["frags"] >= self._MIN_FRAMES for e in self._parts.values())
        except Exception:
            return False


# Module-level singleton: the live listener feeds it, the popup reads it.
rx_partials = RxPartialStore()
