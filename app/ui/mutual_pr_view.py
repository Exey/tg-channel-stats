"""Mutual PR (ad-swap) view: every tracked channel in one sortable table —
followers, an estimated ad-post follower-gain forecast at five horizons
(24h highlighted, since it's the number most ad-swap decisions hinge on),
a "Repeated after Month" column estimating a second/reminder post placed
a month after the first (app.scoring_pr.repeated_post_forecast), and the
least-crowded weekdays to post one, each with a rate showing how much
better that day scores than an average day for the channel. Meant to help
decide which channels are actually worth trading ad posts with.

Recent content quality ("Interest" in app.scoring_pr) isn't shown as its
own column, but still feeds the forecast/best-days math under the hood —
see channel_interest's weight in follow_conversion_rate and best_days.

All figures beyond Followers are estimates built from app.scoring_pr, which
documents exactly which parts are measured (this app's own quality formula)
versus assumed heuristics (the view-accumulation curve and the ad
conversion rate) — see that module's docstring. mutual_pr_hint surfaces the
same caveat in the UI itself, since this table is meant to inform deals with
other people's channels, not just describe your own.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QScrollArea, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..folders import FolderStore
from ..scoring_pr import ad_forecast, best_days, channel_interest, repeated_post_forecast
from ..store import ChannelStore
from .dashboard_view import fmt_int
from .theme import COLORS
from .widgets import SectionCard, hline

# Forecast columns after the Repeated-after-Month column, in table order.
_FORECAST_COLS = ["48h", "72h", "week", "month"]
_WD_KEYS = ["wd_mon", "wd_tue", "wd_wed", "wd_thu", "wd_fri", "wd_sat", "wd_sun"]
_TITLE_MAX_CHARS = 24
_TITLE_COL_WIDTH = 170
_FOLLOWERS_COL = 0
_TITLE_COL = 1
_COL_24H = 2   # tinted — see _render_table
_COL_REPEATED = 3
_FORECAST_START_COL = 4   # 48h onward, see _FORECAST_COLS
_TINTED_COL = _COL_24H
_BEST_DAYS_COL = 8
_BEST_DAYS_COL_WIDTH = 190   # fits e.g. "Wed(x1.58), Thu(x1.42)" without eliding

# col index -> sortable (Best days isn't a single scalar).
_SORTABLE_COLS = {0, 1, 2, 3, 4, 5, 6, 7}


def _channel_label(ch: dict) -> str:
    username = ch.get("username") or ""
    if username:
        return f"@{username}"
    return ch.get("title") or ch.get("channel") or ch.get("key", "?")


def _truncate(text: str, limit: int = _TITLE_MAX_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _fmt_rate_pct(rate: float) -> str:
    return f"{round((rate - 1) * 100):+d}%"


class MutualPrView(QWidget):
    def __init__(self, i18n, folder_store: FolderStore, channel_store: ChannelStore,
                parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.folder_store = folder_store
        self.channel_store = channel_store
        self._entries: list[dict] = []
        self._sort_col = _TINTED_COL   # 24h forecast — see _render_table
        self._sort_desc = True
        self._build_ui()

    def tr_(self, key: str, **kw) -> str:
        return self.i18n.tr(key, **kw)

    # --------------------------------------------------------------- build
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

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
        self.title_lbl = QLabel(self.tr_("nav_mutual_pr"))
        self.title_lbl.setObjectName("pageTitle")
        header.addWidget(self.title_lbl)
        self.sub_lbl = QLabel(self.tr_("mutual_pr_sub"))
        self.sub_lbl.setObjectName("pageSub")
        header.addWidget(self.sub_lbl)
        page.addLayout(header)

        self.hint_lbl = QLabel(self.tr_("mutual_pr_hint"))
        self.hint_lbl.setObjectName("hint")
        self.hint_lbl.setWordWrap(True)
        page.addWidget(self.hint_lbl)

        pick_row = QHBoxLayout()
        self.pick_lbl = QLabel(self.tr_("mutual_pr_pick_folder"))
        pick_row.addWidget(self.pick_lbl)
        self.folder_combo = QComboBox()
        self.folder_combo.currentIndexChanged.connect(self._on_folder_changed)
        pick_row.addWidget(self.folder_combo, 1)
        page.addLayout(pick_row)
        page.addWidget(hline())

        self.empty_lbl = QLabel(self.tr_("mutual_pr_empty"))
        self.empty_lbl.setObjectName("navEmpty")
        self.empty_lbl.setWordWrap(True)
        page.addWidget(self.empty_lbl)

        page.addWidget(self._table_card(), 100)
        page.addStretch(1)

        self.page_scroll.setWidget(page_holder)

    def _table_card(self) -> SectionCard:
        card = SectionCard(self.tr_("nav_mutual_pr"))
        self.table_card_ref = card

        self.table = QTableWidget(0, 9)
        self._set_headers()
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        # Fixed rather than Stretch — a Stretch title column claimed all the
        # leftover width once Interest/2-Weeks were dropped, crowding out
        # the other columns; a title beyond _TITLE_MAX_CHARS is elided with
        # a tooltip instead (see _render_table).
        header.setSectionResizeMode(_TITLE_COL, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(_TITLE_COL, _TITLE_COL_WIDTH)
        self.table.setColumnWidth(_BEST_DAYS_COL, _BEST_DAYS_COL_WIDTH)
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._on_header_clicked)
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # No internal scrollbar: the table grows to fit every row (see
        # _sync_table_height) and the page-level scroll area handles the
        # rest, same as FolderStatView's period table.
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        card.body.addWidget(self.table, 1)
        return card

    def _set_headers(self) -> None:
        self.table.setHorizontalHeaderLabels([
            self.tr_("col_followers"), self.tr_("col_channel_title"),
            self.tr_("col_forecast_24h"), self.tr_("col_repeated_after_month"),
            self.tr_("col_forecast_48h"), self.tr_("col_forecast_72h"),
            self.tr_("col_forecast_week"), self.tr_("col_forecast_month"),
            self.tr_("col_best_days")])

    # --------------------------------------------------------------- data
    def refresh(self) -> None:
        """Reload folders + recompute every channel's Mutual PR figures from
        disk. Call whenever the view is shown, or folders/channels changed
        elsewhere."""
        current_id = self.folder_combo.currentData()
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        self.folder_combo.addItem(self.tr_("mutual_pr_all_channels"), None)
        for folder in self.folder_store.list_folders():
            self.folder_combo.addItem(folder["name"], folder["id"])
        self.folder_combo.blockSignals(False)
        idx = self.folder_combo.findData(current_id) if current_id else 0
        self.folder_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._reload_entries()

    def _on_folder_changed(self, _index: int) -> None:
        self._render_table()

    def _reload_entries(self) -> None:
        """Compute once per channel — header-click sorting only re-sorts
        this cached list, never recomputes it (see _render_table)."""
        self._entries = []
        for summary in self.channel_store.list():
            data = self.channel_store.load(summary["key"])
            if not data:
                continue
            data.setdefault("key", summary["key"])
            stats = data.get("stats", {}) or {}
            avg_views = float(stats.get("avg_views", 0) or 0)
            avg_views_settled = float(stats.get("avg_views_settled", 0) or 0)
            avg_posts_per_day = float(stats.get("avg_posts_per_day", 0) or 0)
            weekday_counts = data.get("distributions", {}).get("weekday") or [0] * 7
            interest = channel_interest(data.get("rows", []) or [], avg_views)
            forecast = ad_forecast(avg_views_settled, interest, avg_posts_per_day)
            self._entries.append({
                "channel": data,
                "folder_id": self.folder_store.folder_for_channel(data["key"]),
                "followers": int(data.get("info", {}).get("members", 0) or 0),
                "forecast": forecast,
                "repeated": repeated_post_forecast(forecast["24h"], avg_posts_per_day),
                "best_days": best_days(weekday_counts, interest),
            })
        self._render_table()

    def _visible_entries(self) -> list[dict]:
        folder_id = self.folder_combo.currentData()
        if folder_id is None:
            return self._entries
        return [e for e in self._entries if e["folder_id"] == folder_id]

    def _sort_value(self, entry: dict, col: int):
        if col == _FOLLOWERS_COL:
            return entry["followers"]
        if col == _TITLE_COL:
            return _channel_label(entry["channel"]).lower()
        if col == _COL_24H:
            return entry["forecast"]["24h"]
        if col == _COL_REPEATED:
            return entry["repeated"]
        if _FORECAST_START_COL <= col < _BEST_DAYS_COL:
            return entry["forecast"][_FORECAST_COLS[col - _FORECAST_START_COL]]
        return 0

    def _on_header_clicked(self, col: int) -> None:
        if col not in _SORTABLE_COLS:
            return
        if col == self._sort_col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col, self._sort_desc = col, True
        self._render_table()

    def _render_table(self) -> None:
        entries = sorted(self._visible_entries(),
                         key=lambda e: self._sort_value(e, self._sort_col),
                         reverse=self._sort_desc)

        self.empty_lbl.setVisible(not entries)
        self.table_card_ref.setVisible(bool(entries))
        self.table.setRowCount(len(entries))
        tint = QColor(COLORS["accent_soft"])
        for i, entry in enumerate(entries):
            ch = entry["channel"]
            label = _channel_label(ch)
            title_item = QTableWidgetItem(_truncate(label))
            title_item.setToolTip(label)
            self.table.setItem(i, _TITLE_COL, title_item)
            self.table.setItem(i, _FOLLOWERS_COL, QTableWidgetItem(fmt_int(entry["followers"])))

            item_24h = QTableWidgetItem(fmt_int(round(entry["forecast"]["24h"])))
            item_24h.setBackground(tint)
            self.table.setItem(i, _COL_24H, item_24h)
            self.table.setItem(i, _COL_REPEATED,
                               QTableWidgetItem(fmt_int(round(entry["repeated"]))))
            for j, horizon in enumerate(_FORECAST_COLS):
                item = QTableWidgetItem(fmt_int(round(entry["forecast"][horizon])))
                self.table.setItem(i, _FORECAST_START_COL + j, item)

            days_label = ", ".join(f"{self.tr_(_WD_KEYS[d])}({_fmt_rate_pct(r)})"
                                   for d, r in entry["best_days"])
            self.table.setItem(i, _BEST_DAYS_COL, QTableWidgetItem(days_label))

        order = Qt.SortOrder.DescendingOrder if self._sort_desc else Qt.SortOrder.AscendingOrder
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSortIndicator(self._sort_col, order)
        self._sync_table_height()

    def _sync_table_height(self) -> None:
        total = self.table.horizontalHeader().height()
        for r in range(self.table.rowCount()):
            total += self.table.rowHeight(r)
        total += 2 * self.table.frameWidth()
        self.table.setMinimumHeight(total)

    # ---------------------------------------------------------- translate
    def retranslate(self) -> None:
        self.title_lbl.setText(self.tr_("nav_mutual_pr"))
        self.sub_lbl.setText(self.tr_("mutual_pr_sub"))
        self.hint_lbl.setText(self.tr_("mutual_pr_hint"))
        self.pick_lbl.setText(self.tr_("mutual_pr_pick_folder"))
        self.empty_lbl.setText(self.tr_("mutual_pr_empty"))
        self.table_card_ref.title_lbl.setText(self.tr_("nav_mutual_pr"))
        current_id = self.folder_combo.currentData()
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        self.folder_combo.addItem(self.tr_("mutual_pr_all_channels"), None)
        for folder in self.folder_store.list_folders():
            self.folder_combo.addItem(folder["name"], folder["id"])
        self.folder_combo.blockSignals(False)
        idx = self.folder_combo.findData(current_id) if current_id is not None else 0
        self.folder_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._set_headers()
        self._render_table()
