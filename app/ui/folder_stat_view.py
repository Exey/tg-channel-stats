"""Folder Stat view: for one folder of channels, show reposts between them
and per-period (monthly/seasonal) totals exportable as Markdown.

Both tables are built from what's already in each channel's checkpoint, not a
fresh Telegram fetch. The links table relies on the checkpoint's stored top-N
sample, so it's only as complete as the top-N and "include public reposts"
choices made when the channel was fetched. Everything in the period table —
views/shares/reactions/viral-share and the "most viewed post" — comes from
`distributions.monthly`, which the fetcher fills from *every* scanned post,
so it's accurate regardless of top-N, as long as the channel was fetched
after that field was added (older checkpoints report zero/blank there until
refetched).

The month/season picker buttons are deliberately built from *every* tracked
channel, not just the ones in the currently selected folder — so the grid of
available periods stays put as you switch folders; only the numbers below it
change.
"""
from __future__ import annotations

import re
from datetime import datetime

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..folders import FolderStore
from ..periods import (
    YEAR_WINDOW_OPTIONS, period_key_label as _period_key_label, year_window_cutoff,
)
from ..store import ChannelStore
from .dashboard_view import build_post_link, fmt_int
from .widgets import SectionCard, hline

MONTHS_FULL = ["", "January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]


def _parse_date(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def _extract_ident(link: str) -> str:
    m = re.search(r"t\.me/(?:c/)?([^/?#\s]+)", str(link or ""))
    return m.group(1).lower() if m else ""


def _channel_uid(ch: dict) -> str:
    username = ch.get("username") or ""
    return f"@{username}" if username else (ch.get("channel") or ch.get("key", ""))


def _channel_title(ch: dict) -> str:
    return ch.get("title") or ch.get("channel") or ch.get("key", "")


def _channel_label(ch: dict) -> str:
    username = ch.get("username") or ""
    if username:
        return f"@{username}"
    return ch.get("title") or ch.get("channel") or ch.get("key", "?")


class FolderStatView(QWidget):
    # period_table column index -> sortable (Most viewed post / Name / Website aren't).
    _PERIOD_SORTABLE_COLS = {0, 1, 2, 3, 4, 6, 7}

    def __init__(self, i18n, folder_store: FolderStore, channel_store: ChannelStore,
                 parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.folder_store = folder_store
        self.channel_store = channel_store
        self._channels: list[dict] = []
        self._periods: dict[tuple, dict] = {}
        self._period_mode = "season"
        self._selected_period_key: tuple | None = None
        self._period_btns: dict[tuple, QPushButton] = {}
        self._year_window_key = "all"
        self._year_btns: dict[str, QPushButton] = {}
        self._period_entries: list[dict] = []
        self._period_sort_col = 6   # Viral share, matching the previous fixed order
        self._period_sort_desc = True
        self._build_ui()

    def tr_(self, key: str, **kw) -> str:
        return self.i18n.tr(key, **kw)

    # --------------------------------------------------------------- build
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # The whole page scrolls as one unit — the period table used to be
        # the one thing that scrolled internally (fixed to whatever space
        # was left after the header/pickers), which meant a big folder/
        # period got squeezed into a tiny visible slice. Now the table
        # grows to its full row count (see _sync_period_table_height) and
        # this scroll area is what pans to the rest of it.
        self.page_scroll = QScrollArea()
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(self.page_scroll)

        page_holder = QWidget()
        page = QVBoxLayout(page_holder)
        page.setContentsMargins(34, 28, 40, 24)
        page.setSpacing(16)

        header = QVBoxLayout()
        header.setSpacing(2)
        self.title_lbl = QLabel(self.tr_("nav_folder_stat"))
        self.title_lbl.setObjectName("pageTitle")
        header.addWidget(self.title_lbl)
        self.sub_lbl = QLabel(self.tr_("folder_stat_sub"))
        self.sub_lbl.setObjectName("pageSub")
        header.addWidget(self.sub_lbl)
        page.addLayout(header)

        pick_row = QHBoxLayout()
        self.pick_lbl = QLabel(self.tr_("folder_stat_pick_folder"))
        pick_row.addWidget(self.pick_lbl)
        self.folder_combo = QComboBox()
        self.folder_combo.currentIndexChanged.connect(self._on_folder_changed)
        pick_row.addWidget(self.folder_combo, 1)
        page.addLayout(pick_row)
        page.addWidget(hline())

        self.no_folders_lbl = QLabel(self.tr_("folder_stat_no_folders"))
        self.no_folders_lbl.setObjectName("navEmpty")
        self.no_folders_lbl.setWordWrap(True)
        page.addWidget(self.no_folders_lbl)

        self.empty_channels_lbl = QLabel(self.tr_("folder_stat_empty_channels"))
        self.empty_channels_lbl.setObjectName("navEmpty")
        self.empty_channels_lbl.setWordWrap(True)
        page.addWidget(self.empty_channels_lbl)

        self.content = QWidget()
        body = QVBoxLayout(self.content)
        body.setContentsMargins(0, 6, 0, 0)
        body.setSpacing(20)
        # content's stretch is wildly disproportionate (100 vs. the trailing
        # spacer's 1) on purpose: with two equal-stretch=1 items, Qt splits
        # leftover space between them roughly evenly, which used to leave
        # the cards stopping well short of the window's bottom edge when
        # there's little enough data that the page doesn't need to scroll.
        # The 100:1 ratio makes content claim essentially all of it while
        # keeping the spacer's stretch nonzero — needed for the *hidden*
        # case below.
        page.addWidget(self.content, 100)
        # A trailing addStretch — not just content's own stretch — because
        # when content is hidden (no folders/no channels yet, see
        # _reload_channels) Qt orphans a hidden widget's stretch instead of
        # honoring it, and spreads the leftover height across the other,
        # visible items instead; this always-present spacer item is what
        # actually keeps the empty-state message pinned to the top.
        page.addStretch(1)

        body.addWidget(self._links_card())
        body.addWidget(self._period_card(), 1)

        self.page_scroll.setWidget(page_holder)

    def _links_card(self) -> SectionCard:
        card = SectionCard(self.tr_("folder_stat_links_title"))
        self.links_card_ref = card

        self.links_hint_lbl = QLabel(self.tr_("folder_stat_links_hint"))
        self.links_hint_lbl.setObjectName("hint")
        self.links_hint_lbl.setWordWrap(True)
        card.body.addWidget(self.links_hint_lbl)

        self.links_empty_lbl = QLabel(self.tr_("folder_stat_links_empty"))
        self.links_empty_lbl.setObjectName("hint")
        card.body.addWidget(self.links_empty_lbl)

        self.links_table = QTableWidget(0, 5)
        self.links_table.setHorizontalHeaderLabels([
            self.tr_("col_link_source"), self.tr_("col_link_target"),
            self.tr_("col_link_reposts"), self.tr_("col_views"),
            self.tr_("col_link_example")])
        self.links_table.verticalHeader().setVisible(False)
        self.links_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.links_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.links_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.links_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self.links_table.cellDoubleClicked.connect(self._open_link_example)
        self.links_table.setMaximumHeight(220)   # keep the period table as the tall one
        card.body.addWidget(self.links_table)
        return card

    def _period_card(self) -> SectionCard:
        card = SectionCard(self.tr_("folder_stat_period_title"))
        self.period_card_ref = card

        self.period_hint_lbl = QLabel(self.tr_("folder_stat_period_hint"))
        self.period_hint_lbl.setObjectName("hint")
        self.period_hint_lbl.setWordWrap(True)

        self.mode_season_btn = QPushButton(self.tr_("period_mode_season"))
        self.mode_season_btn.setObjectName("ghost")
        self.mode_season_btn.setCheckable(True)
        self.mode_halfyear_btn = QPushButton(self.tr_("period_mode_halfyear"))
        self.mode_halfyear_btn.setObjectName("ghost")
        self.mode_halfyear_btn.setCheckable(True)
        self.mode_month_btn = QPushButton(self.tr_("period_mode_month"))
        self.mode_month_btn.setObjectName("ghost")
        self.mode_month_btn.setCheckable(True)
        self.mode_year_btn = QPushButton(self.tr_("period_mode_year"))
        self.mode_year_btn.setObjectName("ghost")
        self.mode_year_btn.setCheckable(True)
        self._mode_btn_group = QButtonGroup(self)
        self._mode_btn_group.setExclusive(True)
        self._mode_btn_group.addButton(self.mode_season_btn)
        self._mode_btn_group.addButton(self.mode_halfyear_btn)
        self._mode_btn_group.addButton(self.mode_month_btn)
        self._mode_btn_group.addButton(self.mode_year_btn)

        self.period_md_btn = QPushButton(self.tr_("save_md_button"))
        self.period_md_btn.clicked.connect(self._save_md)

        self.picker_container = QWidget()
        self.picker_lay = QVBoxLayout(self.picker_container)
        self.picker_lay.setContentsMargins(0, 0, 0, 0)
        self.picker_lay.setSpacing(8)

        self.period_empty_lbl = QLabel(self.tr_("folder_stat_period_empty"))
        self.period_empty_lbl.setObjectName("hint")

        self.period_table = QTableWidget(0, 10)
        self.period_table.setHorizontalHeaderLabels([
            self.tr_("col_channel_title"), self.tr_("col_username_id"), self.tr_("col_views"),
            self.tr_("col_shares"), self.tr_("col_reactions"), self.tr_("col_most_viewed"),
            self.tr_("col_viral_share"), self.tr_("col_rating"), self.tr_("col_name"),
            self.tr_("col_website")])
        self.period_table.verticalHeader().setVisible(False)
        self.period_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.period_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        period_header = self.period_table.horizontalHeader()
        period_header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        period_header.setSectionsClickable(True)
        period_header.sectionClicked.connect(self._on_period_header_clicked)
        self.period_table.cellDoubleClicked.connect(self._open_period_post)
        self.period_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # No internal scrollbar: the table grows to fit every row (see
        # _sync_period_table_height) and the page-level scroll area handles
        # anything taller than the viewport, instead of the table scrolling
        # on its own within a fixed slice of the page.
        self.period_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # Every widget the toggle handler touches now exists, so it's safe to
        # wire the signal and set the default mode (which fires it once).
        self.mode_season_btn.toggled.connect(lambda checked: self._on_mode_toggled("season", checked))
        self.mode_halfyear_btn.toggled.connect(
            lambda checked: self._on_mode_toggled("halfyear", checked))
        self.mode_month_btn.toggled.connect(lambda checked: self._on_mode_toggled("month", checked))
        self.mode_year_btn.toggled.connect(lambda checked: self._on_mode_toggled("year", checked))
        self.mode_season_btn.setChecked(True)

        mode_row = QHBoxLayout()
        mode_row.addWidget(self.mode_season_btn)
        mode_row.addWidget(self.mode_halfyear_btn)
        mode_row.addWidget(self.mode_month_btn)
        mode_row.addWidget(self.mode_year_btn)
        mode_row.addStretch()
        mode_row.addWidget(self.period_md_btn)

        card.body.addWidget(self.period_hint_lbl)
        card.body.addLayout(mode_row)
        card.body.addWidget(self.picker_container)
        card.body.addWidget(self.period_empty_lbl)
        card.body.addWidget(self.period_table, 1)
        return card

    # --------------------------------------------------------------- data
    def refresh(self) -> None:
        """Reload folders + this folder's channels from disk. Call whenever
        the view is shown, or folders/channels changed elsewhere."""
        current_id = self.folder_combo.currentData()
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        for folder in self.folder_store.list_folders():
            self.folder_combo.addItem(folder["name"], folder["id"])
        self.folder_combo.blockSignals(False)
        if self.folder_combo.count():
            idx = self.folder_combo.findData(current_id) if current_id else -1
            self.folder_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._reload_channels()

    def _on_folder_changed(self, _index: int) -> None:
        self._reload_channels()

    def _reload_channels(self) -> None:
        folder_id = self.folder_combo.currentData()
        self._channels = []
        if folder_id:
            keys = [k for k, fid in self.folder_store.assignments.items() if fid == folder_id]
            for key in keys:
                data = self.channel_store.load(key)
                if data:
                    data.setdefault("key", key)
                    self._channels.append(data)

        has_folders = self.folder_combo.count() > 0
        has_channels = bool(self._channels)
        self.no_folders_lbl.setVisible(not has_folders)
        self.pick_lbl.setVisible(has_folders)
        self.folder_combo.setVisible(has_folders)
        self.empty_channels_lbl.setVisible(has_folders and not has_channels)
        self.content.setVisible(has_channels)

        self._rebuild_links_table()
        self._rebuild_period_picker()

    # -------------------------------------------------------------- links
    def _collect_links(self) -> list[dict]:
        index: dict[str, dict] = {}
        for ch in self._channels:
            uname = (ch.get("username") or "").lower()
            cid = str(ch.get("info", {}).get("id") or "")
            if uname:
                index[uname] = ch
            if cid:
                index[cid] = ch

        edges: dict[tuple, dict] = {}
        for ch in self._channels:
            for row in ch.get("rows", []) or []:
                pub = row.get("public")
                if not pub or pub.get("count", 0) <= 0:
                    continue
                for item in pub.get("items", []) or []:
                    link = item.get("link", "")
                    target = index.get(_extract_ident(link))
                    if not target or target.get("key") == ch.get("key"):
                        continue
                    key = (ch["key"], target["key"])
                    edge = edges.setdefault(key, {
                        "source": _channel_label(ch), "target": _channel_label(target),
                        "count": 0, "views": 0, "example": link,
                    })
                    edge["count"] += 1
                    edge["views"] += int(item.get("views", 0) or 0)
        return sorted(edges.values(), key=lambda e: e["count"], reverse=True)

    def _rebuild_links_table(self) -> None:
        edges = self._collect_links()
        self.links_empty_lbl.setVisible(not edges)
        self.links_table.setVisible(bool(edges))
        self.links_table.setRowCount(len(edges))
        for i, edge in enumerate(edges):
            self.links_table.setItem(i, 0, QTableWidgetItem(edge["source"]))
            self.links_table.setItem(i, 1, QTableWidgetItem(edge["target"]))
            self.links_table.setItem(i, 2, QTableWidgetItem(fmt_int(edge["count"])))
            self.links_table.setItem(i, 3, QTableWidgetItem(fmt_int(edge["views"])))
            example = QTableWidgetItem(self.tr_("show"))
            example.setToolTip(edge["example"])
            example.setData(Qt.ItemDataRole.UserRole, edge["example"])
            self.links_table.setItem(i, 4, example)

    def _open_link_example(self, row: int, _col: int) -> None:
        item = self.links_table.item(row, 4)
        link = item.data(Qt.ItemDataRole.UserRole) if item else None
        if link:
            QDesktopServices.openUrl(QUrl(link))

    # ------------------------------------------------------------- periods
    def _collect_periods(self, mode: str) -> dict[tuple, dict]:
        """Per-period totals for the *currently selected folder's* channels.

        Everything — views/shares/reactions/viral-share and the "most viewed
        post" — comes from each checkpoint's `distributions.monthly`, which
        the fetcher fills from *every* scanned post, not just the stored
        top-N sample in `rows`. Checkpoints fetched before these fields
        existed report zero/blank here until refetched.
        """
        periods: dict[tuple, dict] = {}
        for ch in self._channels:
            buckets: dict[tuple, dict] = {}
            for m in ch.get("distributions", {}).get("monthly") or []:
                count = int(m.get("count", 0) or 0)
                if not count:
                    continue
                try:
                    year, month = (int(x) for x in m.get("label", "").split("-"))
                except ValueError:
                    continue
                if mode == "year":
                    # Everything within the selected rolling window collapses
                    # into a single bucket — month-granularity, since that's
                    # all `distributions.monthly` can offer.
                    cutoff = year_window_cutoff(self._year_window_days())
                    if cutoff is not None and (year, month) < (cutoff.year, cutoff.month):
                        continue
                    key, label = ("year", self._year_window_key), self._year_window_label()
                else:
                    key, label = _period_key_label(year, month, mode)
                b = buckets.setdefault(key, {
                    "label": label, "count": 0, "views": 0, "shares": 0,
                    "reactions": 0, "viral_count": 0, "top_row": None,
                })
                b["count"] += count
                b["views"] += int(m.get("views", 0) or 0)
                b["shares"] += int(m.get("shares", 0) or 0)
                b["reactions"] += int(m.get("reactions", 0) or 0)
                b["viral_count"] += int(m.get("viral_count", 0) or 0)
                top = m.get("top")
                if top and (b["top_row"] is None
                           or int(top.get("views", 0) or 0) > int(b["top_row"].get("views", 0) or 0)):
                    b["top_row"] = top

            for key, b in buckets.items():
                bucket = periods.setdefault(key, {"label": b["label"], "entries": []})
                bucket["entries"].append({
                    "channel": ch, "views": b["views"], "shares": b["shares"],
                    "reactions": b["reactions"], "top_row": b["top_row"],
                    "viral_share": b["viral_count"] / b["count"] * 100,
                })
        for bucket in periods.values():
            self._score_entries(bucket["entries"])
        return periods

    _VIRALITY_WEIGHT_MIN = 0.20
    _VIRALITY_WEIGHT_MAX = 0.51
    _ENGAGEMENT_WEIGHT = 0.20

    @staticmethod
    def _virality_component(viral_share: float) -> float:
        """0-1 virality score from an *absolute* viral-share percentage (not
        normalized against other channels): 0% -> 0, 1% -> 0.05, ramping up
        to 1.0 at 15%+ viral share."""
        if viral_share <= 0:
            return 0.0
        if viral_share >= 15:
            return 1.0
        if viral_share <= 1:
            return 0.05 * viral_share
        return 0.05 + (viral_share - 1) * (1.0 - 0.05) / (15 - 1)

    @staticmethod
    def _virality_weight(viral_share: float) -> float:
        """How much of the rating virality can swing, itself scaled by how
        viral the channel is: 20% at 0% viral share, ramping linearly to
        51% at 15%+ viral share — so a highly viral channel isn't just
        scored well on virality, virality also matters more to its rating."""
        lo, hi = FolderStatView._VIRALITY_WEIGHT_MIN, FolderStatView._VIRALITY_WEIGHT_MAX
        if viral_share <= 0:
            return lo
        if viral_share >= 15:
            return hi
        return lo + (viral_share / 15) * (hi - lo)

    @staticmethod
    def _score_entries(entries: list[dict]) -> None:
        """Composite 0-1 rating per entry: a fixed 20% engagement, a
        virality weight that itself ramps from 20% to 51% with viral share
        (see `_virality_weight`) applied to the virality score (see
        `_virality_component`), and period views taking whatever weight is
        left (80% - virality weight) — so views weight shrinks as virality
        weight grows. Engagement and views are min-max normalized against
        the other channels in the same period bucket; virality uses an
        absolute viral-share curve so it doesn't get diluted by how other
        channels in the folder performed.
        """
        if not entries:
            return
        views_vals = [e["views"] for e in entries]
        eng_vals = [e["shares"] + e["reactions"] for e in entries]
        vw_min, vw_max = min(views_vals), max(views_vals)
        eg_min, eg_max = min(eng_vals), max(eng_vals)
        for e in entries:
            views_norm = (e["views"] - vw_min) / (vw_max - vw_min) if vw_max != vw_min else 0
            eng = e["shares"] + e["reactions"]
            eng_norm = (eng - eg_min) / (eg_max - eg_min) if eg_max != eg_min else 0
            viral_score = FolderStatView._virality_component(e["viral_share"])
            virality_weight = FolderStatView._virality_weight(e["viral_share"])
            views_weight = (1.0 - FolderStatView._ENGAGEMENT_WEIGHT) - virality_weight
            e["score"] = (FolderStatView._ENGAGEMENT_WEIGHT * eng_norm
                         + virality_weight * viral_score
                         + views_weight * views_norm)

    def _collect_all_period_keys(self, mode: str) -> list[tuple[tuple, str]]:
        """Every period key/label across *all* tracked channels, regardless of
        folder — this is what drives the picker buttons, so the grid of
        available periods doesn't change when you switch folders."""
        keys: dict[tuple, str] = {}
        for summary in self.channel_store.list():
            data = self.channel_store.load(summary["key"])
            if not data:
                continue
            for m in data.get("distributions", {}).get("monthly") or []:
                if not int(m.get("count", 0) or 0):
                    continue
                try:
                    year, month = (int(x) for x in m.get("label", "").split("-"))
                except ValueError:
                    continue
                key, label = _period_key_label(year, month, mode)
                keys[key] = label
        return sorted(keys.items(), key=lambda kv: kv[0], reverse=True)

    # ------------------------------------------------------- period picker
    @staticmethod
    def _clear_layout(layout) -> None:
        # hide() first — takeAt() only unmanages a widget from the layout,
        # it doesn't hide it, and deleteLater() doesn't actually destroy it
        # until the next event-loop pass, so a rapid double-rebuild (the
        # constructor's initial setChecked(True) followed by main_window's
        # first refresh(), both before the event loop is re-entered) can
        # leave the old buttons visibly stacked under the new ones.
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                FolderStatView._clear_layout(sub)

    def _on_mode_toggled(self, mode: str, checked: bool) -> None:
        if not checked:
            return
        self._period_mode = mode
        self._selected_period_key = None
        self._rebuild_period_picker()

    def _make_period_button(self, text: str, key: tuple) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("ghost")
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._period_btn_group.addButton(btn)
        btn.toggled.connect(lambda checked, k=key: self._on_period_button_toggled(k, checked))
        self._period_btns[key] = btn
        return btn

    def _build_season_picker(self, keys_labels: list[tuple[tuple, str]]) -> None:
        row_widget = QWidget()
        row_lay = QHBoxLayout(row_widget)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(6)
        for key, label in keys_labels:
            row_lay.addWidget(self._make_period_button(label, key))
        row_lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(row_widget)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedHeight(48)
        self.picker_lay.addWidget(scroll)

    def _build_month_picker(self, keys_labels: list[tuple[tuple, str]]) -> None:
        years = sorted({key[0] for key, _ in keys_labels}, reverse=True)

        grid_widget = QWidget()
        grid_lay = QVBoxLayout(grid_widget)
        grid_lay.setContentsMargins(0, 0, 0, 0)
        grid_lay.setSpacing(6)
        for year in years:
            year_row = QHBoxLayout()
            year_row.setSpacing(6)
            year_lbl = QLabel(f"{year}:")
            year_lbl.setMinimumWidth(52)
            year_lbl.setStyleSheet("font-weight: 700;")
            year_row.addWidget(year_lbl)
            for m in range(1, 13):
                year_row.addWidget(self._make_period_button(MONTHS_FULL[m], (year, m)))
            year_row.addStretch()
            grid_lay.addLayout(year_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(grid_widget)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFixedHeight(min(220, 46 * max(len(years), 1) + 10))
        self.picker_lay.addWidget(scroll)

    def _year_window_days(self) -> int | None:
        for key, _label_key, days in YEAR_WINDOW_OPTIONS:
            if key == self._year_window_key:
                return days
        return None

    def _year_window_label(self) -> str:
        for key, label_key, _days in YEAR_WINDOW_OPTIONS:
            if key == self._year_window_key:
                return self.tr_(label_key)
        return ""

    def _build_year_picker(self) -> None:
        row_widget = QWidget()
        row_lay = QHBoxLayout(row_widget)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(6)
        self._year_btn_group = QButtonGroup(self)
        self._year_btn_group.setExclusive(True)
        self._year_btns = {}
        for key, label_key, _days in YEAR_WINDOW_OPTIONS:
            btn = QPushButton(self.tr_(label_key))
            btn.setObjectName("ghost")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._year_btn_group.addButton(btn)
            btn.toggled.connect(lambda checked, k=key: self._on_year_window_toggled(k, checked))
            self._year_btns[key] = btn
            row_lay.addWidget(btn)
        row_lay.addStretch()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(row_widget)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedHeight(48)
        self.picker_lay.addWidget(scroll)
        self._year_btns[self._year_window_key].setChecked(True)

    def _on_year_window_toggled(self, key: str, checked: bool) -> None:
        if not checked:
            return
        self._year_window_key = key
        self._selected_period_key = ("year", key)
        self._rebuild_selected_period_table()

    def _rebuild_period_picker(self) -> None:
        self._clear_layout(self.picker_lay)
        self._period_btn_group = QButtonGroup(self)
        self._period_btn_group.setExclusive(True)
        self._period_btns = {}
        self.picker_container.setVisible(True)

        if self._period_mode == "year":
            self._build_year_picker()  # its own setChecked(True) triggers the rebuild
            return

        keys_labels = self._collect_all_period_keys(self._period_mode)
        if self._period_mode in ("season", "halfyear"):
            # Both are a flat, single-scroll row of buttons (one per
            # period key) — the season picker's builder works unchanged
            # for half-years too, unlike the month picker's per-year grid.
            self._build_season_picker(keys_labels)
        else:
            self._build_month_picker(keys_labels)

        if keys_labels:
            target = (self._selected_period_key if self._selected_period_key in self._period_btns
                      else keys_labels[0][0])
            self._period_btns[target].setChecked(True)
        else:
            self._selected_period_key = None
            self._rebuild_selected_period_table()

    def _on_period_button_toggled(self, key: tuple, checked: bool) -> None:
        if not checked:
            return
        self._selected_period_key = key
        self._rebuild_selected_period_table()

    def _rebuild_selected_period_table(self) -> None:
        self._periods = self._collect_periods(self._period_mode)
        bucket = self._periods.get(self._selected_period_key) if self._selected_period_key else None
        self._period_entries = list(bucket["entries"]) if bucket else []
        self._render_period_table()

    def _period_sort_value(self, entry: dict, col: int):
        ch = entry["channel"]
        if col == 0:
            return _channel_title(ch).lower()
        if col == 1:
            return _channel_uid(ch).lower()
        if col == 2:
            return entry["views"]
        if col == 3:
            return entry["shares"]
        if col == 4:
            return entry["reactions"]
        if col == 6:
            return entry["viral_share"]
        if col == 7:
            return entry["score"]
        return 0

    def _on_period_header_clicked(self, col: int) -> None:
        if col not in self._PERIOD_SORTABLE_COLS:
            return
        if col == self._period_sort_col:
            self._period_sort_desc = not self._period_sort_desc
        else:
            self._period_sort_col, self._period_sort_desc = col, True
        self._render_period_table()

    def _render_period_table(self) -> None:
        entries = sorted(self._period_entries,
                         key=lambda e: self._period_sort_value(e, self._period_sort_col),
                         reverse=self._period_sort_desc)

        self.period_empty_lbl.setVisible(not entries)
        self.period_table.setVisible(bool(entries))
        self.period_table.setRowCount(len(entries))
        for i, entry in enumerate(entries):
            ch = entry["channel"]
            self.period_table.setItem(i, 0, QTableWidgetItem(_channel_title(ch)))
            self.period_table.setItem(i, 1, QTableWidgetItem(_channel_uid(ch)))
            self.period_table.setItem(i, 2, QTableWidgetItem(fmt_int(entry["views"])))
            self.period_table.setItem(i, 3, QTableWidgetItem(fmt_int(entry["shares"])))
            self.period_table.setItem(i, 4, QTableWidgetItem(fmt_int(entry["reactions"])))

            top_row = entry["top_row"]
            if top_row is None:
                post_item = QTableWidgetItem("—")
            else:
                snippet = (top_row.get("text") or f"#{top_row.get('id')}").strip()
                if len(snippet) > 60:
                    snippet = snippet[:59] + "…"
                link = build_post_link(ch.get("channel") or ch.get("username") or "",
                                       top_row.get("id", 0))
                post_item = QTableWidgetItem(snippet or f"#{top_row.get('id')}")
                post_item.setToolTip(link)
                post_item.setData(Qt.ItemDataRole.UserRole, link)
            self.period_table.setItem(i, 5, post_item)

            self.period_table.setItem(i, 6, QTableWidgetItem(f"{entry['viral_share']:.4f}%"))
            self.period_table.setItem(i, 7, QTableWidgetItem(f"{entry['score']:.3f}"))
            self.period_table.setItem(i, 8, QTableWidgetItem(""))
            self.period_table.setItem(i, 9, QTableWidgetItem(""))

        order = (Qt.SortOrder.DescendingOrder if self._period_sort_desc
                 else Qt.SortOrder.AscendingOrder)
        self.period_table.horizontalHeader().setSortIndicatorShown(True)
        self.period_table.horizontalHeader().setSortIndicator(self._period_sort_col, order)
        self._sync_period_table_height()

    def _sync_period_table_height(self) -> None:
        """Grow period_table to fit every row with no internal scrollbar
        (see its setVerticalScrollBarPolicy in _period_card) — height still
        only a *minimum*, so QSizePolicy.Expanding can inflate it further
        to fill the page when there's little enough data that the page
        doesn't need to scroll (see _build_ui's 100:1 stretch)."""
        total = self.period_table.horizontalHeader().height()
        for r in range(self.period_table.rowCount()):
            total += self.period_table.rowHeight(r)
        total += 2 * self.period_table.frameWidth()
        self.period_table.setMinimumHeight(total)

    def _open_period_post(self, row: int, _col: int) -> None:
        item = self.period_table.item(row, 5)
        link = item.data(Qt.ItemDataRole.UserRole) if item else None
        if link:
            QDesktopServices.openUrl(QUrl(link))

    # ------------------------------------------------------------- export
    def _build_md(self) -> str:
        headers = [self.tr_("col_username_id"), self.tr_("col_views"), self.tr_("col_shares"),
                   self.tr_("col_reactions"), self.tr_("col_most_viewed"),
                   self.tr_("col_viral_share"), self.tr_("col_rating"),
                   self.tr_("col_name"), self.tr_("col_website")]
        lines = [f"# {self.folder_combo.currentText()}", ""]
        for key in sorted(self._periods):
            bucket = self._periods[key]
            lines.append(f"## {bucket['label']}")
            lines.append("")
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
            for entry in sorted(bucket["entries"], key=lambda e: e["viral_share"], reverse=True):
                ch = entry["channel"]
                top_row = entry["top_row"]
                if top_row is None:
                    post_md = "—"
                else:
                    text = (top_row.get("full_text") or top_row.get("text")
                           or f"#{top_row.get('id')}")
                    text = (text.replace("|", "\\|").replace("\n", " ").strip()
                           or f"#{top_row.get('id')}")
                    link = build_post_link(ch.get("channel") or ch.get("username") or "",
                                           top_row.get("id", 0))
                    post_md = f"[{text}]({link})"
                lines.append(
                    f"| {_channel_uid(ch)} | {entry['views']} | {entry['shares']} | "
                    f"{entry['reactions']} | {post_md} | {entry['viral_share']:.4f}% | "
                    f"{entry['score']:.3f} |  |  |")
            lines.append("")
        return "\n".join(lines).rstrip("\n") + "\n"

    def _save_md(self) -> None:
        if not self._periods:
            QMessageBox.information(self, self.tr_("app_title"),
                                    self.tr_("folder_stat_period_empty"))
            return
        default = f"{self.folder_combo.currentText() or 'folder'}_stats.md"
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr_("save_md_button"), default, "Markdown (*.md)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._build_md())
        except OSError as exc:
            QMessageBox.warning(self, self.tr_("app_title"), str(exc))
            return
        QMessageBox.information(self, self.tr_("app_title"),
                                self.tr_("md_saved", path=path))

    # ---------------------------------------------------------- translate
    def retranslate(self) -> None:
        self.title_lbl.setText(self.tr_("nav_folder_stat"))
        self.sub_lbl.setText(self.tr_("folder_stat_sub"))
        self.pick_lbl.setText(self.tr_("folder_stat_pick_folder"))
        self.no_folders_lbl.setText(self.tr_("folder_stat_no_folders"))
        self.empty_channels_lbl.setText(self.tr_("folder_stat_empty_channels"))

        self.links_card_ref.title_lbl.setText(self.tr_("folder_stat_links_title"))
        self.links_hint_lbl.setText(self.tr_("folder_stat_links_hint"))
        self.links_empty_lbl.setText(self.tr_("folder_stat_links_empty"))
        self.links_table.setHorizontalHeaderLabels([
            self.tr_("col_link_source"), self.tr_("col_link_target"),
            self.tr_("col_link_reposts"), self.tr_("col_views"),
            self.tr_("col_link_example")])

        self.period_card_ref.title_lbl.setText(self.tr_("folder_stat_period_title"))
        self.period_hint_lbl.setText(self.tr_("folder_stat_period_hint"))
        self.mode_season_btn.setText(self.tr_("period_mode_season"))
        self.mode_halfyear_btn.setText(self.tr_("period_mode_halfyear"))
        self.mode_month_btn.setText(self.tr_("period_mode_month"))
        self.mode_year_btn.setText(self.tr_("period_mode_year"))
        for key, btn in self._year_btns.items():
            label_key = next(lk for k, lk, _d in YEAR_WINDOW_OPTIONS if k == key)
            btn.setText(self.tr_(label_key))
        self.period_md_btn.setText(self.tr_("save_md_button"))
        self.period_empty_lbl.setText(self.tr_("folder_stat_period_empty"))
        self.period_table.setHorizontalHeaderLabels([
            self.tr_("col_channel_title"), self.tr_("col_username_id"), self.tr_("col_views"),
            self.tr_("col_shares"), self.tr_("col_reactions"), self.tr_("col_most_viewed"),
            self.tr_("col_viral_share"), self.tr_("col_rating"), self.tr_("col_name"),
            self.tr_("col_website")])
