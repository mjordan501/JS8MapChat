# main_window_group_activity.py — Group Activity viewer for JS8FastChat
#
# Mixin carved from MainWindow (Phase 1 T2): the Group Activity popup + its detail
# window and the ~14 row-classification / dedupe / formatting helpers they use.
# These methods are heavily bound to the live app (widget factory, layout memory,
# selection/sort/status, the QSO popup), so they stay methods and resolve through
# MainWindow's MRO — the JS8Map _XxxMixin pattern, not a free-function module.
#
# The mixin never imports main_window (no cycle); it re-imports the siblings its
# bodies use, replicating the monolith's exact aliases (tkinter as tk). Method
# bodies are byte-identical to the originals.

from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk, filedialog
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..constants import (
    APP_VERSION, DEFAULT_GROUPS, GROUP_ALL, LAYOUT_PATH, TIME_FILTERS,
)
from ..models import ActivityRow
from ..utils import base_call, debug_exc, fmt_freq, parse_ts, write_json


class _GroupActivityMixin:
    """Group Activity popup + detail viewer and their row-classification helpers.
    Mixed into MainWindow; all state and sibling methods resolve via self/MRO."""

    # group activity/detail viewer
    def _group_activity_groups_for_row(self, row: ActivityRow, group_map: Optional[dict] = None) -> list[str]:
        """Return all known @GROUP tags for a row/callsign, not just the first one."""
        groups: set[str] = set()
        for value in (row.group, row.to_call):
            text = str(value or "").upper().strip()
            if text.startswith("@"):
                groups.add(text)
        for match in re.findall(r"@[A-Z0-9_\-]{2,}", str(row.text or "").upper()):
            groups.add(match)
        if group_map:
            try:
                groups.update(str(g).upper() for g in group_map.get(row.base, set()) if str(g or "").startswith("@"))
            except Exception:
                pass
        return sorted(g for g in groups if g and g != "@HB")

    def _group_activity_group_display(self, row: ActivityRow, group_map: Optional[dict] = None) -> str:
        groups = self._group_activity_groups_for_row(row, group_map)
        return ", ".join(groups) if groups else str(row.group or row.to_call or "")

    def _group_activity_normalized_text(self, row: ActivityRow) -> str:
        """Normalize activity text for de-duplication, not display.

        Strip sender prefixes, RSNR brackets, and stray decode characters such
        as the common Â artifact so directed/band copies of the same RF event
        collapse correctly.
        """
        text = str(row.text or "").upper().strip()
        call = base_call(row.call)
        if call:
            text = re.sub(rf"^\s*{re.escape(call)}\s*:\s*", "", text)
        text = re.sub(r"\[RSNR:[^\]]+\]", "", text, flags=re.I)
        text = text.replace("Â", " ")
        text = re.sub(r"[^A-Z0-9@+\-? ]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _group_activity_snr_key(self, row: ActivityRow) -> str:
        text = self._group_activity_normalized_text(row)
        match = re.search(r"\bSNR\s*([+\-]?\d+)", text)
        if match:
            return match.group(1)
        raw = str(row.snr or "").strip()
        return raw.replace(".0", "") if raw else text


    def _group_activity_is_snr_hb(self, row: ActivityRow) -> bool:
        text = str(row.text or "").upper()
        if "HEARTBEAT" in text or "@HB" in str(row.to_call or "").upper() or "@HB" in str(row.group or "").upper():
            return True
        if "[RSNR:" in text:
            return True
        if " SNR?" in f" {text} ":
            return True
        if re.search(r"\bSNR\s*[+\-]?\d+", text):
            return True
        return False

    def _group_activity_is_query(self, row: ActivityRow) -> bool:
        text = f" {str(row.text or '').upper()} "
        return " QUERY " in text or " QUERY CALL" in text or " QUERY MSG" in text or " QUERY MSGS" in text

    def _group_activity_is_msg(self, row: ActivityRow) -> bool:
        text = f" {str(row.text or '').upper()} "
        if " STORE MSG " in text or " MSG " in text or int(getattr(row, "inbox_count", 0) or 0) > 0:
            return not self._group_activity_is_snr_hb(row)
        return False

    def _group_activity_is_report(self, row: ActivityRow) -> bool:
        # Use normalized traffic text and whole-word matching so callsigns such
        # as N4WXI do not trip the WX report keyword.
        text = self._group_activity_normalized_text(row)
        terms = (
            "REPORT", "SITREP", "STATUS", "WELFARE", "CHECK IN", "CHECK-IN", "CHECKIN",
            "NET", "TRAFFIC", "POWER", "OUTAGE", "MEDICAL", "NEED", "SUPPLY", "REQUEST",
            "DAMAGE", "WX", "WEATHER", "ROAD", "FUEL", "WATER", "COMMS", "RADIO", "RELAY",
        )
        for term in terms:
            phrase = term.replace("-", " ")
            if re.search(r"\b" + re.escape(phrase) + r"\b", text):
                return True
        return False


    def _group_activity_is_form(self, row: ActivityRow) -> bool:
        text = str(row.text or "").upper()
        if " FORM " in f" {text} " or "MSG CODE" in text or "MESSAGE CODE" in text:
            return True
        # Future parser hook: many forms are coded; for now tag obvious compact form-code patterns.
        return bool(re.search(r"\b[A-Z]{2,8}[-_ ]?\d{1,4}\b", text) and any(word in text for word in ("FORM", "REPORT", "SITREP", "STATUS")))

    def _group_activity_tag(self, row: ActivityRow) -> str:
        tags: list[str] = []
        my_call = base_call(self.locator.callsign())
        if my_call and base_call(row.to_call) == my_call:
            tags.append("TO ME")
        if getattr(row, "watch_hit", ""):
            tags.append("WATCH")
        if self._group_activity_is_form(row):
            tags.append("FORM")
        elif self._group_activity_is_report(row):
            tags.append("REPORT")
        if self._group_activity_is_msg(row):
            tags.append("MSG")
        elif self._group_activity_is_query(row):
            tags.append("QUERY")
        elif self._group_activity_is_snr_hb(row):
            tags.append("SNR/HB")
        elif str(row.source or "").lower() == "directed" or row.to_call:
            tags.append("DIRECTED")
        return " ".join(dict.fromkeys(tags)) or "RAW"

    def _group_activity_matches_show_filter(self, row: ActivityRow, show_filter: str, tag: str) -> bool:
        show = str(show_filter or "Important").upper().strip()
        if show == "ALL RAW":
            return True
        if show == "TO ME":
            return "TO ME" in tag
        if show == "DIRECTED MSG":
            return "MSG" in tag or ("DIRECTED" in tag and "SNR/HB" not in tag and "QUERY" not in tag)
        if show == "REPORTS / FORMS":
            return "REPORT" in tag or "FORM" in tag
        if show == "WATCH HITS":
            return "WATCH" in tag
        if show == "QUERIES":
            return "QUERY" in tag
        if show == "SNR / HB":
            return "SNR/HB" in tag
        # Important: meaningful directed/group traffic. Routine TO ME SNR/HB and plain queries are noise.
        if "SNR/HB" in tag or "QUERY" in tag:
            return any(key in tag for key in ("WATCH", "REPORT", "FORM", "MSG"))
        return any(key in tag for key in ("TO ME", "MSG", "REPORT", "FORM", "WATCH", "DIRECTED"))

    def _row_matches_group_activity(self, row: ActivityRow, group: str, term: str = "", group_map: Optional[dict] = None, show_filter: str = "Important") -> bool:
        """Return True when an activity row belongs in the Group Activity viewer."""
        group = str(group or GROUP_ALL).upper().strip()
        term = str(term or "").upper().strip()
        row_text = str(row.text or "").upper()
        row_source = str(row.source or "").upper()

        if (row.text or "").strip().upper() == "SPOT":
            return False

        groups = self._group_activity_groups_for_row(row, group_map)
        row_to = str(row.to_call or "").upper().strip()
        is_group_row = (
            bool(groups)
            or row_to.startswith("@")
            or re.search(r"@[A-Z0-9_\-]{2,}", row_text) is not None
            or "DIRECTED" in row_source
            or row.source == "directed"
            or bool(row.to_call)
        )
        if not is_group_row:
            return False

        if group and group != GROUP_ALL.upper() and group != GROUP_ALL:
            if group not in groups and group != row_to and group not in row_text:
                return False

        tag = self._group_activity_tag(row)
        if not self._group_activity_matches_show_filter(row, show_filter, tag):
            return False

        if term:
            haystack = " ".join(str(x or "") for x in (
                row.timestamp, row.call, row.to_call, self._group_activity_group_display(row, group_map),
                row.snr, fmt_freq(row.freq), row.offset, row.source, tag, row.text,
            )).upper()
            if term not in haystack:
                return False
        return True

    def _group_activity_dedupe_rows(self, rows: list[ActivityRow]) -> list[ActivityRow]:
        """Collapse repeated rows from directed/band_activity while keeping the best version.

        JS8Map can store the same RF event in directed and band tables, often
        one second apart.  SNR/HB rows are aggressively collapsed because they
        are status noise, not message traffic.
        """
        best: dict[tuple, ActivityRow] = {}
        scores: dict[tuple, int] = {}

        def score(row: ActivityRow) -> int:
            value = 0
            text = str(row.text or "").upper()
            if str(row.source or "").lower() == "directed":
                value += 40
            if row.to_call:
                value += 15
            if row.group:
                value += 8
            if "[RSNR:" not in text:
                value += 10
            if ":" in str(row.text or ""):
                value += 4
            try:
                if float(row.offset or 0) != 0:
                    value += 2
            except Exception:
                pass
            if self._group_activity_is_msg(row) or self._group_activity_is_report(row) or self._group_activity_is_form(row):
                value += 20
            return value

        def key_for(row: ActivityRow) -> tuple:
            norm = self._group_activity_normalized_text(row)
            call = base_call(row.call)
            to = base_call(row.to_call)
            timestamp_minute = str(row.timestamp or "")[:16]
            freq = fmt_freq(row.freq)
            group = str(row.group or "").upper().strip()
            if self._group_activity_is_snr_hb(row):
                # SNR/HB duplicates often arrive one second apart with different raw text.
                return ("SNRHB", call, to, self._group_activity_snr_key(row), freq, group)
            if self._group_activity_is_query(row):
                return ("QUERY", timestamp_minute, call, to, norm)
            return ("TRAFFIC", timestamp_minute, call, to, norm)

        for row in rows:
            key = key_for(row)
            row_score = score(row)
            if key not in best or row_score > scores.get(key, -1):
                best[key] = row
                scores[key] = row_score
        out = list(best.values())
        out.sort(key=lambda r: str(r.timestamp or ""), reverse=True)
        return out

    def _group_activity_timestamp_utc(self, value: object) -> str:
        """Display JS8Map/Windows naive timestamps as UTC for radio logging.

        JS8Map rows usually arrive as local Windows time while the app banner is
        UTC.  Treat naive DB timestamps as local time and convert for this
        Group Activity viewer so the table matches operator UTC workflow.
        """
        dt = parse_ts(value)
        if not dt:
            return str(value or "")
        try:
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        except Exception:
            return str(value or "")

    def _activity_row_detail_text(self, row: ActivityRow, group_map: Optional[dict] = None) -> str:
        """Build copyable wrapped text for one Group Activity row."""
        tag = self._group_activity_tag(row)
        lines = [
            "Message Details:",
            "",
            f"Tag:        {tag}",
            f"Call:       {row.call}",
            f"To:         {row.to_call or ''}",
            f"Group:      {self._group_activity_group_display(row, group_map)}",
            f"Dial/Freq:  {fmt_freq(row.freq)}",
            f"Offset:     {row.offset}",
            f"Date UTC:   {self._group_activity_timestamp_utc(row.timestamp)}",
            f"SNR:        {row.snr}",
            f"Type:       {row.source}",
            "",
            "Text:",
            str(row.text or ""),
        ]
        return "\n".join(lines)

    def open_group_activity_popup(self) -> None:
        """Open a read-only directed/group traffic intelligence viewer."""
        p = self.pal()
        top = tk.Toplevel(self)
        top.title(f"Group Activity / Detail — Ver {APP_VERSION}")
        top.configure(bg=p.panel)
        self.bind_popup_layout(top, "group_activity_geometry", "1320x760")

        group_values = list(self.groups or DEFAULT_GROUPS)
        if GROUP_ALL not in group_values:
            group_values.insert(0, GROUP_ALL)
        start_group = self.group_filter_var.get() if self.group_filter_var.get() in group_values else GROUP_ALL
        group_var = tk.StringVar(value=start_group)
        time_var = tk.StringVar(value=self.db_time_var.get())
        show_values = ["Important", "To Me", "Directed Msg", "Reports / Forms", "Watch Hits", "Queries", "SNR / HB", "All Raw"]
        show_var = tk.StringVar(value="Important")
        search_var = tk.StringVar(value="")
        row_map: dict[str, ActivityRow] = {}
        group_map_holder: dict[str, dict] = {"map": {}}

        toolbar = tk.Frame(top, bg=p.panel)
        toolbar.pack(fill="x", padx=8, pady=(8, 6))
        filter_row = tk.Frame(toolbar, bg=p.panel)
        filter_row.pack(fill="x", pady=(0, 4))
        action_row = tk.Frame(toolbar, bg=p.panel)
        action_row.pack(fill="x")

        tk.Label(filter_row, text="Group:", bg=p.panel, fg=p.text, font=self.scaled_font(12, "bold")).pack(side="left", padx=(0, 4))
        group_cb = self.combo(filter_row, group_var, group_values, width=16)
        group_cb.pack(side="left", padx=(0, 12))
        tk.Label(filter_row, text="DB Time:", bg=p.panel, fg=p.text, font=self.scaled_font(12, "bold")).pack(side="left", padx=(0, 4))
        time_cb = self.combo(filter_row, time_var, list(TIME_FILTERS.keys()), width=15)
        time_cb.pack(side="left", padx=(0, 12))
        tk.Label(filter_row, text="Show:", bg=p.panel, fg=p.text, font=self.scaled_font(12, "bold")).pack(side="left", padx=(0, 4))
        show_cb = self.combo(filter_row, show_var, show_values, width=17)
        show_cb.pack(side="left", padx=(0, 8))

        tk.Label(action_row, text="Search:", bg=p.panel, fg=p.text, font=self.scaled_font(12, "bold")).pack(side="left", padx=(0, 4))
        search_frame = tk.Frame(action_row, bg=p.entry_bg, highlightbackground=("#ffffff" if self.theme_var.get() == "Dark" else p.border), highlightcolor=("#ffffff" if self.theme_var.get() == "Dark" else p.border), highlightthickness=1, bd=0)
        search_frame.pack(side="left", padx=(0, 6))
        search_entry = tk.Entry(search_frame, textvariable=search_var, width=24, bg=p.entry_bg, fg=p.entry_fg, insertbackground=p.entry_fg, relief="flat", bd=0, font=self.scaled_font(11, "bold", family="Consolas"))
        search_entry.pack(side="left", fill="x", expand=True, padx=(4, 0), ipady=3)
        search_clear_btn = tk.Button(search_frame, text="X", command=lambda: clear_search(), bg=p.entry_bg, fg=p.entry_fg, activebackground=p.button_active, activeforeground=p.entry_fg, relief="flat", bd=0, font=self.scaled_font(9, "bold"), width=2)
        search_clear_btn.pack(side="left", padx=(2, 3))

        panes = tk.PanedWindow(top, orient="vertical", sashwidth=7, sashrelief="raised", opaqueresize=True, bd=0, bg=p.border)
        panes.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        tree_frame = tk.Frame(panes, bg=p.panel)
        detail_frame = tk.Frame(panes, bg=p.panel)
        panes.add(tree_frame, minsize=220)
        panes.add(detail_frame, minsize=150)

        cols = ("when", "tag", "from", "to", "group", "snr", "freq", "offset", "type", "text")
        tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="browse")
        specs = [
            ("when", "When UTC", 175, "w"),
            ("tag", "Tag", 120, "w"),
            ("from", "From", 90, "w"),
            ("to", "To", 90, "w"),
            ("group", "Group", 145, "w"),
            ("snr", "SNR", 55, "center"),
            ("freq", "Freq", 80, "center"),
            ("offset", "Offset", 70, "center"),
            ("type", "Type", 90, "w"),
            ("text", "Text", 500, "w"),
        ]
        for col, label, width, anchor in specs:
            tree.heading(col, text=label, command=lambda c=col: self.sort_treeview(tree, c))
            tree.column(col, width=width, anchor=anchor, stretch=(col == "text"))
        tree.pack(fill="both", expand=True)

        saved_visible = self._layout().get("group_activity_visible_columns") or list(cols)
        if not isinstance(saved_visible, list):
            saved_visible = list(cols)
        visible_col_vars = {col: tk.BooleanVar(value=(col in saved_visible)) for col in cols}

        def apply_group_activity_columns(save: bool = True) -> None:
            visible = [col for col in cols if visible_col_vars[col].get()]
            if not visible:
                visible = ["when", "tag", "from", "text"]
                for col in visible:
                    if col in visible_col_vars:
                        visible_col_vars[col].set(True)
            tree.configure(displaycolumns=visible)
            if save:
                with self.soft("save_group_activity_visible_columns"):
                    data = dict(self._layout())
                    data["group_activity_visible_columns"] = visible
                    data["saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self._layout_data = data
                    write_json(LAYOUT_PATH, data)

        def open_group_columns_popup() -> None:
            win = tk.Toplevel(top)
            win.title("Group Activity Columns")
            win.configure(bg=p.panel)
            try:
                win.transient(top)
            except Exception:
                pass
            tk.Label(win, text="Show / hide Group Activity columns", bg=p.panel, fg=p.text, font=self.scaled_font(13, "bold"), anchor="w").pack(fill="x", padx=12, pady=(12, 6))
            labels = {"when": "When UTC", "tag": "Tag", "from": "From", "to": "To", "group": "Group", "snr": "SNR", "freq": "Freq", "offset": "Offset", "type": "Type", "text": "Text"}
            for col in cols:
                tk.Checkbutton(win, text=labels.get(col, col), variable=visible_col_vars[col], bg=p.panel, fg=p.text, activebackground=p.panel, activeforeground=p.text, selectcolor=p.panel2, font=self.scaled_font(11, "bold"), command=lambda: apply_group_activity_columns(save=True)).pack(anchor="w", padx=16, pady=2)
            btns = tk.Frame(win, bg=p.panel)
            btns.pack(fill="x", padx=12, pady=(8, 12))
            def show_all() -> None:
                for var in visible_col_vars.values():
                    var.set(True)
                apply_group_activity_columns(save=True)
            self.button(btns, "Show All", command=show_all, width=10).pack(side="left")
            self.button(btns, "Close", command=win.destroy, width=8).pack(side="right")

        apply_group_activity_columns(save=False)

        detail_label = tk.Label(detail_frame, text="Selected row detail", bg=p.panel, fg=p.text, font=self.scaled_font(13, "bold"), anchor="w")
        detail_label.pack(fill="x", padx=0, pady=(0, 2))
        detail = self.text_box(detail_frame, height=7, font=self.scaled_font(13, "bold", family="Consolas"), wrap="word")
        detail.pack(fill="both", expand=True)
        detail.insert("1.0", "Default Show=Important hides routine SNR/HB/query clutter. Select a row for details.")
        detail.configure(state="disabled")
        detail_buttons = tk.Frame(detail_frame, bg=p.panel)
        detail_buttons.pack(fill="x", pady=(4, 0))

        def save_group_activity_layout(_event=None) -> None:
            if self._closing:
                return
            with self.soft("save_group_activity_layout"):
                data = dict(self._layout())
                try:
                    _x, y = panes.sash_coord(0)
                    data["group_activity_sash_y"] = int(y)
                except Exception:
                    pass
                data["group_activity_columns"] = self.capture_tree_column_widths(tree)
                data["group_activity_visible_columns"] = [col for col in cols if visible_col_vars[col].get()]
                data["saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._layout_data = data
                write_json(LAYOUT_PATH, data)

        def restore_group_activity_layout() -> None:
            with self.soft("restore_group_activity_layout"):
                data = self._layout()
                self.restore_tree_column_widths("group_activity_columns", tree, data=data)
                apply_group_activity_columns(save=False)
                y = data.get("group_activity_sash_y")
                if y is not None:
                    panes.sash_place(0, 0, max(160, int(y)))

        top.after(300, restore_group_activity_layout)
        panes.bind("<ButtonRelease-1>", lambda e: top.after(120, save_group_activity_layout), add="+")
        tree.bind("<ButtonRelease-1>", lambda e: top.after(120, save_group_activity_layout), add="+")
        tree.bind("<Configure>", lambda e: top.after(250, save_group_activity_layout), add="+")

        def selected_row() -> Optional[ActivityRow]:
            sel = tree.selection()
            if not sel:
                return None
            return row_map.get(str(sel[0]))

        def load_rows(_event=None) -> None:
            with self.soft("open_group_activity_popup.load_rows"):
                tree.delete(*tree.get_children())
                row_map.clear()
                cutoff = self.reader.cutoff_string(time_var.get())
                try:
                    group_map_holder["map"] = self.reader.group_activity_map(cutoff)
                except Exception:
                    group_map_holder["map"] = {}
                    debug_exc("group_activity group_map")
                rows = self.reader.read_activity(time_var.get(), group_filter=GROUP_ALL, limit=2000, include_counts=False)
                rows = self._group_activity_dedupe_rows(rows)
                group = group_var.get()
                term = search_var.get()
                show_filter = show_var.get()
                shown = []
                for row in rows:
                    if self._row_matches_group_activity(row, group, term, group_map_holder["map"], show_filter):
                        shown.append(row)
                for idx, row in enumerate(shown):
                    iid = f"grp_{idx}_{base_call(row.call)}"
                    row_map[iid] = row
                    tag = self._group_activity_tag(row)
                    tree.insert("", "end", iid=iid, values=(
                        self._group_activity_timestamp_utc(row.timestamp),
                        tag,
                        row.call,
                        row.to_call,
                        self._group_activity_group_display(row, group_map_holder["map"]),
                        row.snr,
                        fmt_freq(row.freq),
                        row.offset,
                        row.source,
                        row.text,
                    ), tags=("watched",) if row.watched else ())
                detail.configure(state="normal")
                detail.delete("1.0", "end")
                detail.insert("1.0", f"{len(shown)} row(s) shown. Show={show_filter}. Duplicate decode rows are collapsed. Double-click a row for full detail.")
                detail.configure(state="disabled")
                self.set_status(f"Group Activity viewer: {len(shown)} row(s) for {group_var.get()} / {show_filter}.")
                top.after(120, save_group_activity_layout)

        def clear_search() -> None:
            search_var.set("")
            load_rows()

        def update_detail(_event=None) -> None:
            row = selected_row()
            detail.configure(state="normal")
            detail.delete("1.0", "end")
            if row:
                detail.insert("1.0", self._activity_row_detail_text(row, group_map_holder["map"]))
                self.call_info._color_from_marker_red(detail, "Text:", tag="detail_red")
            else:
                detail.insert("1.0", "Default Show=Important hides routine SNR/HB/query clutter. Select a row for details.")
            detail.configure(state="disabled")

        def open_detail(_event=None) -> None:
            row = selected_row()
            if row:
                self.open_group_activity_detail(row, parent=top, group_map=group_map_holder["map"])

        def copy_selected() -> None:
            row = selected_row()
            if not row:
                self.set_status("No Group Activity row selected to copy.")
                return
            text = self._activity_row_detail_text(row, group_map_holder["map"])
            self.clipboard_clear()
            self.clipboard_append(text)
            self.set_status("Group Activity row copied to clipboard.")

        def save_selected_detail() -> None:
            row = selected_row()
            if not row:
                self.set_status("No Group Activity row selected to save.")
                return
            text = self._activity_row_detail_text(row, group_map_holder["map"])
            def choose_group_activity_export_folder() -> Optional[Path]:
                data = dict(self._layout())
                saved = str(data.get("group_activity_export_dir", "") or "").strip()
                if saved:
                    saved_path = Path(saved)
                    if saved_path.exists():
                        return saved_path
                initial = Path.home() / "Documents"
                if not initial.exists():
                    initial = Path(__file__).resolve().parents[2]
                folder = filedialog.askdirectory(title="Choose folder for Group Activity saved details", initialdir=str(initial), parent=top)
                if not folder:
                    self.set_status("Save Detail canceled; no folder selected.")
                    return None
                chosen = Path(folder)
                data["group_activity_export_dir"] = str(chosen)
                data["saved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._layout_data = data
                write_json(LAYOUT_PATH, data)
                return chosen

            exports = choose_group_activity_export_folder()
            if exports is None:
                return
            exports.mkdir(parents=True, exist_ok=True)
            def safe_part(value: object, default: str = "ROW") -> str:
                raw = str(value or default).upper().strip()
                raw = raw.replace("@", "")
                raw = re.sub(r"[^A-Z0-9._-]+", "_", raw).strip("_")
                return raw[:60] or default
            stamp = safe_part(self._group_activity_timestamp_utc(row.timestamp).replace(" UTC", "").replace(":", "-").replace(" ", "_"), "TIME")
            call = safe_part(base_call(row.call), "CALL")
            group = safe_part(self._group_activity_group_display(row, group_map_holder["map"]), "GROUP")
            tag = safe_part(self._group_activity_tag(row).replace("/", "-"), "TAG")
            path = exports / f"{stamp}_{call}_{group}_{tag}.txt"
            path.write_text(text, encoding="utf-8")
            self.set_status(f"Saved Group Activity detail: {path}")

        def fastchat_selected() -> None:
            row = selected_row()
            if not row:
                self.set_status("No Group Activity row selected for FastChat.")
                return
            call = base_call(row.call)
            if not call:
                self.set_status("Selected row has no usable callsign.")
                return
            self.set_selected_call(call, source="group_activity")
            self.open_qso_popup()

        self.button(action_row, "GO", command=load_rows, width=5, good=True).pack(side="left", padx=(0, 6))
        self.button(action_row, "Refresh", command=load_rows, width=9, good=True).pack(side="left", padx=(0, 6))
        self.button(action_row, "Open FastChat", command=fastchat_selected, width=13).pack(side="left", padx=(0, 6))
        self.button(action_row, "Copy Row", command=copy_selected, width=10).pack(side="left", padx=(0, 6))
        self.button(action_row, "Columns", command=open_group_columns_popup, width=9).pack(side="left", padx=(0, 6))
        self.button(action_row, "Close", command=top.destroy, width=8).pack(side="right")

        self.button(detail_buttons, "Open FastChat", command=fastchat_selected, width=14).pack(side="left", padx=(0, 6), ipady=3)
        self.button(detail_buttons, "Copy Detail", command=copy_selected, width=12).pack(side="left", padx=(0, 6), ipady=3)
        self.button(detail_buttons, "Save Detail", command=save_selected_detail, width=12).pack(side="left", padx=(0, 6), ipady=3)

        group_cb.bind("<<ComboboxSelected>>", load_rows)
        time_cb.bind("<<ComboboxSelected>>", load_rows)
        show_cb.bind("<<ComboboxSelected>>", load_rows)
        search_entry.bind("<Return>", load_rows)
        tree.bind("<<TreeviewSelect>>", update_detail)
        tree.bind("<Double-Button-1>", open_detail)

        load_rows()

    def open_group_activity_detail(self, row: ActivityRow, parent=None, group_map: Optional[dict] = None) -> None:
        """Open the full detail view for one group/directed activity row."""
        p = self.pal()
        top = tk.Toplevel(parent or self)
        top.title("Activity Detail")
        top.configure(bg=p.panel)
        self.bind_popup_layout(top, "activity_detail_geometry", "760x540")
        try:
            top.transient(parent or self)
        except Exception:
            pass

        tk.Label(top, text="Activity Detail", bg=p.panel, fg=p.text, font=self.scaled_font(18, "bold"), anchor="w").pack(fill="x", padx=10, pady=(10, 4))
        txt = self.text_box(top, font=self.scaled_font(13, "bold", family="Consolas"), wrap="word")
        txt.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        txt.insert("1.0", self._activity_row_detail_text(row, group_map))
        self.call_info._color_from_marker_red(txt, "Text:", tag="detail_red")
        txt.configure(state="disabled")

        btns = tk.Frame(top, bg=p.panel)
        btns.pack(fill="x", padx=10, pady=(0, 12))

        def copy_text() -> None:
            self.clipboard_clear()
            self.clipboard_append(self._activity_row_detail_text(row, group_map))
            self.set_status("Activity Detail copied to clipboard.")

        def open_fastchat() -> None:
            call = base_call(row.call)
            if call:
                self.set_selected_call(call, source="group_activity_detail")
                self.open_qso_popup()
            else:
                self.set_status("Activity Detail has no usable callsign for FastChat.")

        self.button(btns, "Open FastChat", command=open_fastchat, width=16).pack(side="left", padx=(0, 8), ipady=4)
        self.button(btns, "Copy Text", command=copy_text, width=12).pack(side="left", padx=(0, 8), ipady=4)
        self.button(btns, "OK", command=top.destroy, width=10).pack(side="right", ipady=4)
