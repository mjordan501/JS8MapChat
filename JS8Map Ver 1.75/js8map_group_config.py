"""GroupConfig — extracted verbatim from JS8Map.py.

Moved by tools/extract_class.py. Behavior is byte-identical to the
original class; only its location changed.
"""
from __future__ import annotations

from js8map_runtime import DATA_DIR as _DATA_DIR
import json
import os

class GroupConfig:
    """Maps @GROUP_NAME → hex color. Persisted to js8_groups.json."""
    DEFAULT_GROUPS = {
        '@AMRRON':  '#CC2200', '@EMCOMM':  '#00AA44',
        '@ARES':    '#0077CC', '@RACES':   '#FF8800',
        '@HRMS':    '#9933CC', '@MAGNET':  '#00BBAA',
        '@PREPNET': '#CC6600', '@SKYWARN': '#CCAA00',
        '@MARS':    '#CC0088', '@FIELDDAY':'#4488FF',
    }
    def __init__(self):
        self.path = os.path.join(
            _DATA_DIR, 'js8_groups.json')
        self._data: dict = {}
        self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                self._data = {
                    (k.upper() if k.startswith('@') else '@' + k.upper()): v
                    for k, v in loaded.items()}
                return
            except Exception:
                pass
        self._data = dict(self.DEFAULT_GROUPS)
        self.save()

    def save(self):
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, indent=2)
        except Exception:
            pass

    def get_groups(self) -> dict:
        return dict(self._data)

    def set_groups(self, groups: dict):
        self._data = {
            (k.upper() if k.startswith('@') else '@' + k.upper()): v
            for k, v in groups.items()}
        self.save()
