"""Per-channel dashboard: stat cards, activity charts and the top-posts table.

The layout borrows the analytics_dashboard grid (a row of KPI tiles, a wide
activity chart, paired distribution charts) and renders it from a stored
channel checkpoint. The top-posts table — sortable columns, album merging,
public-repost drill-down, Markdown / text export — is the channel_top feature
set, kept intact.
"""
from __future__ import annotations

import re
from datetime import datetime

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QDialog, QDialogButtonBox,
    QFileDialog, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QMenu,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from PySide6.QtCore import Signal

from ..folders import FolderStore
from ..periods import period_key_label
from .charts import BarChart, MultiLineChart
from .folder_dialog import FolderManagerDialog
from .theme import COLORS
from .widgets import Card, ChartCard, SectionCard, StatCard, folder_icon, hline

MONTHS_SHORT = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

LAST_FULL_YEAR = datetime.now().year - 1

# card key -> i18n key for an explanatory tooltip (same cards/logic as compare mode).
_CARD_TOOLTIPS = {
    "erv_pct": "cmp_erv_pct_tip",
    "virality_index": "cmp_virality_index_tip",
    "viral_post_share": "cmp_viral_share_tip",
}


def fmt_int(n) -> str:
    try:
        return f"{int(n):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(n)


def short_num(n, decimals: int = 1) -> str:
    """Compact form for big counts: 6.6K, 7.15M (decimals=2), …"""
    try:
        v = float(n or 0)
    except (TypeError, ValueError):
        return str(n)
    for div, suf in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs(v) >= div:
            return f"{v / div:.{decimals}f}{suf}"
    return str(int(v))


def build_post_link(channel_text: str, msg_id: int) -> str:
    """t.me link from whatever channel identifier we have (@user, -100…, or
    a full t.me URL — the "channel" field is whatever was typed into the
    fetch form, so it can be any of these; a raw URL passed through
    unstripped used to produce a broken double-prefixed t.me/https://t.me/…
    link)."""
    v = str(channel_text).strip()
    v = re.sub(r"^(https?://)?(www\.)?t\.me/", "", v, flags=re.IGNORECASE)
    m = re.match(r"^c/(\d+)", v)
    if m:
        return f"https://t.me/c/{m.group(1)}/{msg_id}"
    v = v.split("/")[0].split("?")[0].lstrip("@")
    if v.startswith("-100") and v[4:].isdigit():
        return f"https://t.me/c/{v[4:]}/{msg_id}"
    if v.lstrip("-").isdigit():
        return f"https://t.me/c/{v.lstrip('-')}/{msg_id}"
    return f"https://t.me/{v}/{msg_id}" if v else f"https://t.me/c/0/{msg_id}"


# ---------------------------------------------------------- popup dialogs

class PublicForwardsDialog(QDialog):
    def __init__(self, parent, i18n, msg_id: int, items: list[dict]) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.setWindowTitle(i18n.tr("public_title", id=msg_id))
        self.resize(560, 360)
        root = QVBoxLayout(self)
        if not items:
            root.addWidget(QLabel(i18n.tr("public_empty")))
        else:
            self.table = QTableWidget(len(items), 3)
            self.table.setHorizontalHeaderLabels([
                i18n.tr("public_col_channel"), i18n.tr("public_col_views"),
                i18n.tr("public_col_link")])
            self.table.verticalHeader().setVisible(False)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            self.table.cellDoubleClicked.connect(self._open_row)
            for row, it in enumerate(items):
                self.table.setItem(row, 0, QTableWidgetItem(it.get("title", "")))
                self.table.setItem(row, 1, QTableWidgetItem(fmt_int(it.get("views", 0))))
                link_item = QTableWidgetItem(it.get("link", ""))
                link_item.setToolTip(it.get("link", ""))
                self.table.setItem(row, 2, link_item)
            root.addWidget(self.table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)

    def _open_row(self, row: int, _col: int) -> None:
        item = self.table.item(row, 2)
        if item and item.text():
            QDesktopServices.openUrl(QUrl(item.text()))


class ChannelReportDialog(QDialog):
    def __init__(self, parent, i18n, text: str, title: str | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self.setWindowTitle(title or i18n.tr("report_dialog_title"))
        self.resize(640, 480)
        root = QVBoxLayout(self)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(text)
        root.addWidget(view)
        row = QHBoxLayout()
        copy_btn = QPushButton(i18n.tr("report_copy"))
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(self._text))
        row.addWidget(copy_btn)
        row.addStretch()
        root.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        root.addWidget(buttons)


# ------------------------------------------------------------- dashboard

class DashboardView(QWidget):
    refetch_requested = Signal(dict)
    remove_requested = Signal(str)
    folders_changed = Signal()

    # table column index -> row-dict key (None = not sortable)
    _SORT_KEYS = {0: "ts", 2: "views", 3: "reactions", 4: "forwards", 5: "public"}

    def __init__(self, i18n, folder_store: FolderStore, parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.folder_store = folder_store
        self._data: dict = {}
        self._rows: list[dict] = []
        self._channel_text = ""
        self._title = ""
        self._sort_col = 2
        self._sort_desc = True
        self._trend_mode = "month"
        self._build_ui()

    def tr_(self, key: str, **kw) -> str:
        return self.i18n.tr(key, **kw)

    def _metric_title(self, key: str, title_key: str) -> str:
        title = self.tr_(title_key)
        if key == "err_pct":
            title = f"{title} ({self.tr_('stat_err_pct_sub')})"
        elif key == "views_last_year":
            title = self.tr_(title_key, year=LAST_FULL_YEAR)
        return title

    # --------------------------------------------------------------- build
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(34, 28, 40, 24)
        outer.setSpacing(16)

        # header row
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title_lbl = QLabel("—")
        self.title_lbl.setObjectName("pageTitle")
        titles.addWidget(self.title_lbl)
        self.sub_lbl = QLabel("")
        self.sub_lbl.setObjectName("pageSub")
        titles.addWidget(self.sub_lbl)
        head.addLayout(titles)
        head.addStretch()

        self.folder_btn = QPushButton(self.tr_("folder_none"))
        self.folder_btn.setObjectName("ghost")
        self.folder_btn.setToolTip(self.tr_("folder_choose"))
        self.folder_btn.clicked.connect(self._show_folder_menu)
        head.addWidget(self.folder_btn)
        self.report_btn = QPushButton(self.tr_("report_button"))
        self.report_btn.clicked.connect(self._show_report)
        head.addWidget(self.report_btn)
        self.md_btn = QPushButton(self.tr_("save_md_button"))
        self.md_btn.clicked.connect(self._save_md)
        head.addWidget(self.md_btn)
        self.refetch_btn = QPushButton(self.tr_("dash_refresh"))
        self.refetch_btn.clicked.connect(self._on_refetch)
        head.addWidget(self.refetch_btn)
        self.remove_btn = QPushButton(self.tr_("dash_remove"))
        self.remove_btn.clicked.connect(self._on_remove)
        head.addWidget(self.remove_btn)
        outer.addLayout(head)
        outer.addWidget(hline())

        # scrollable body
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(0, 6, 8, 0)
        self.body.setSpacing(20)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self._build_stat_cards()
        self._build_top_viral_table()
        self._build_trend_chart()
        self._build_charts()
        self._build_table()

    def _build_stat_cards(self) -> None:
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(18)
        self._cards: dict[str, StatCard] = {}
        specs = [
            ("members", "stat_members"),
            ("total_posts", "stat_total_posts"),
            ("avg_views", "stat_avg_views"),
            ("max_views", "stat_max_views"),
            ("posts_per_day", "stat_posts_per_day"),
            ("avg_reactions", "stat_avg_reactions"),
            ("avg_reposts", "stat_avg_reposts"),
            ("max_reposts", "stat_max_reposts"),
            ("err_pct", "stat_err_pct"),
            ("views_last_year", "cmp_view_repost_year"),
            ("erv_pct", "cmp_erv_pct"),
            ("virality_index", "cmp_virality_index"),
            ("viral_post_share", "cmp_viral_share"),
        ]
        for i, (key, title_key) in enumerate(specs):
            accent = COLORS["accent"] if key != "total_posts" else COLORS["activity"]
            card = StatCard(self._metric_title(key, title_key), accent=accent)
            if key in _CARD_TOOLTIPS:
                card.setToolTip(self.tr_(_CARD_TOOLTIPS[key]))
            self._cards[key] = card
            grid.addWidget(card, i // 4, i % 4)
        for col in range(4):
            grid.setColumnStretch(col, 1)
        self.body.addLayout(grid)

    def _build_top_viral_table(self) -> None:
        self.top_viral_card = SectionCard(self.tr_("top_viral_title"))
        self.top_viral_table = QTableWidget(0, 6)
        self.top_viral_table.setHorizontalHeaderLabels([
            self.tr_("col_date"), self.tr_("col_post"), self.tr_("col_views"),
            self.tr_("col_reactions"), self.tr_("col_private"), self.tr_("col_viral_rate")])
        self.top_viral_table.verticalHeader().setVisible(False)
        self.top_viral_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.top_viral_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.top_viral_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self.top_viral_table.cellDoubleClicked.connect(self._open_viral_row)
        self.top_viral_table.setMinimumHeight(360)  # header + at least 10 rows
        self.top_viral_card.body.addWidget(self.top_viral_table)
        self.body.addWidget(self.top_viral_card)

    def _build_trend_chart(self) -> None:
        self.trend_card = SectionCard(self.tr_("chart_trend_title"))

        self.trend_mode_season_btn = QPushButton(self.tr_("period_mode_season"))
        self.trend_mode_season_btn.setObjectName("ghost")
        self.trend_mode_season_btn.setCheckable(True)
        self.trend_mode_month_btn = QPushButton(self.tr_("period_mode_month"))
        self.trend_mode_month_btn.setObjectName("ghost")
        self.trend_mode_month_btn.setCheckable(True)
        self._trend_mode_group = QButtonGroup(self)
        self._trend_mode_group.setExclusive(True)
        self._trend_mode_group.addButton(self.trend_mode_season_btn)
        self._trend_mode_group.addButton(self.trend_mode_month_btn)

        mode_row = QHBoxLayout()
        mode_row.addWidget(self.trend_mode_season_btn)
        mode_row.addWidget(self.trend_mode_month_btn)
        mode_row.addStretch()

        # Series toggle chips — checkable, colored by series, independent of
        # each other (not a QButtonGroup: any combination can be on). Only
        # Views is on by default since it dwarfs the other two and is what
        # most people want to see first.
        legend_row = QHBoxLayout()
        legend_row.setSpacing(10)
        self._trend_series_visible = {"views": True, "reactions": False, "shares": False}
        self._trend_series_btns: dict[str, QPushButton] = {}
        for key, title_key, color_key in (("views", "col_views", "accent"),
                                          ("reactions", "col_reactions", "weekday"),
                                          ("shares", "col_shares", "warn")):
            btn = QPushButton(f"● {self.tr_(title_key)}")
            btn.setObjectName("ghost")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            color = COLORS[color_key]
            btn.setStyleSheet(
                f"QPushButton#ghost {{ color: {COLORS['faint']}; }}"
                f"QPushButton#ghost:checked {{ color: {color}; background: transparent; "
                f"font-weight: 600; }}"
                f"QPushButton#ghost:hover {{ color: {color}; }}")
            btn.setChecked(self._trend_series_visible[key])
            btn.toggled.connect(lambda checked, k=key: self._on_trend_series_toggled(k, checked))
            legend_row.addWidget(btn)
            self._trend_series_btns[key] = btn
        legend_row.addStretch()

        self.trend_chart = MultiLineChart()
        self.trend_card.body.addLayout(mode_row)
        self.trend_card.body.addLayout(legend_row)
        self.trend_card.body.addWidget(self.trend_chart, 1)
        # Generous floor: title + mode row + legend row + the chart's own
        # minimum (260) + card margins/spacing — too little here silently
        # squeezes the chart below what it needs to fit its x-axis labels.
        self.trend_card.setMinimumHeight(420)
        self.body.addWidget(self.trend_card)

        # Widgets above exist now, so it's safe to wire signals and set the
        # default mode (which fires the toggle once).
        self.trend_mode_season_btn.toggled.connect(
            lambda checked: self._on_trend_mode_toggled("season", checked))
        self.trend_mode_month_btn.toggled.connect(
            lambda checked: self._on_trend_mode_toggled("month", checked))
        self.trend_mode_month_btn.setChecked(True)

    def _build_charts(self) -> None:
        self.activity_chart = BarChart(accent=COLORS["activity"], max_labels=999)
        self.activity_card = ChartCard(self.tr_("chart_activity"), self.activity_chart)
        self.activity_card.setMinimumHeight(300)
        self.body.addWidget(self.activity_card)

        row = QHBoxLayout()
        row.setSpacing(18)
        self.hour_chart = BarChart(accent=COLORS["hour"], max_labels=12)
        self.hour_card = ChartCard(self.tr_("chart_by_hour"), self.hour_chart)
        row.addWidget(self.hour_card, 1)
        self.weekday_chart = BarChart(accent=COLORS["weekday"], max_labels=7)
        self.weekday_card = ChartCard(self.tr_("chart_by_weekday"), self.weekday_chart)
        row.addWidget(self.weekday_card, 1)
        self.body.addLayout(row)

    def _build_table(self) -> None:
        self.table_card = SectionCard(self.tr_("top_posts_title"))
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            self.tr_("col_date"), self.tr_("col_post"), self.tr_("col_views"),
            self.tr_("col_reactions"), self.tr_("col_private"), self.tr_("col_public")])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionsClickable(True)
        header.sectionClicked.connect(self._on_header_clicked)
        self.table.cellDoubleClicked.connect(self._open_row)
        self.table.setMinimumHeight(640)
        self.table_card.body.addWidget(self.table)
        self.body.addWidget(self.table_card)

    # --------------------------------------------------------------- load
    def load(self, data: dict) -> None:
        self._data = data or {}
        self._rows = list(self._data.get("rows", []))
        self._channel_text = self._data.get("channel") or self._data.get("username", "")
        self._title = self._data.get("title", "") or self._channel_text
        self._sort_col, self._sort_desc = 2, True

        self.title_lbl.setText(self._title or "—")
        self.sub_lbl.setText(self._header_sub())

        self._fill_cards()
        self._fill_charts()
        self._rebuild_trend_chart()
        self._rebuild_table()
        self._rebuild_top_viral_table()
        self.refresh_folder_button()

    # -------------------------------------------------------------- folders
    def refresh_folder_button(self) -> None:
        key = self._data.get("key")
        folder_id = self.folder_store.folder_for_channel(key) if key else None
        folder = self.folder_store.get_folder(folder_id) if folder_id else None
        if folder:
            self.folder_btn.setIcon(folder_icon(folder["color"]))
            self.folder_btn.setText(folder["name"])
        else:
            self.folder_btn.setIcon(QIcon())
            self.folder_btn.setText(self.tr_("folder_none"))

    def _show_folder_menu(self) -> None:
        key = self._data.get("key")
        if not key:
            return
        menu = QMenu(self)
        current = self.folder_store.folder_for_channel(key)

        none_act = menu.addAction(self.tr_("folder_none"))
        none_act.setCheckable(True)
        none_act.setChecked(current is None)
        none_act.triggered.connect(lambda: self._assign_folder(None))

        folders = self.folder_store.list_folders()
        if folders:
            menu.addSeparator()
            for folder in folders:
                act = menu.addAction(folder_icon(folder["color"]), folder["name"])
                act.setCheckable(True)
                act.setChecked(folder["id"] == current)
                act.triggered.connect(
                    lambda _=False, fid=folder["id"]: self._assign_folder(fid))

        menu.addSeparator()
        manage_act = menu.addAction(self.tr_("folder_manage"))
        manage_act.triggered.connect(self._open_folder_manager)

        menu.exec(self.folder_btn.mapToGlobal(self.folder_btn.rect().bottomLeft()))

    def _assign_folder(self, folder_id: str | None) -> None:
        key = self._data.get("key")
        if not key:
            return
        self.folder_store.set_channel_folder(key, folder_id)
        self.refresh_folder_button()
        self.folders_changed.emit()

    def _open_folder_manager(self) -> None:
        dlg = FolderManagerDialog(self.folder_store, self.i18n, self)
        dlg.exec()
        self.refresh_folder_button()
        self.folders_changed.emit()

    def _header_sub(self) -> str:
        parts = []
        period = self._data.get("period") or "all"
        parts.append(self.tr_("dash_period_label",
                              period=self.tr_(f"period_{period}")))
        created = self._fmt_date(self._data.get("info", {}).get("created", ""))
        if created:
            parts.append(self.tr_("dash_created_label", when=created))
        when = self._fmt_datetime(self._data.get("fetched_at", ""))
        if when:
            parts.append(self.tr_("dash_fetched_at", when=when))
        link = self._data.get("link")
        if link:
            parts.append(link)
        return "   ·   ".join(parts)

    def _fill_cards(self) -> None:
        info = self._data.get("info", {})
        stats = self._data.get("stats", {})
        monthly = [m["count"] for m in self._data.get("distributions", {}).get("monthly", [])]

        self._cards["members"].set_value(
            fmt_int(info.get("members", 0)) if info.get("members") else "—")
        self._cards["total_posts"].title_lbl.setText(
            self.tr_("stat_total_posts_period", period=self._period_text()))
        self._cards["total_posts"].set_value(
            fmt_int(stats.get("total_posts", 0)),
            spark=monthly if len(monthly) > 2 else None)
        self._cards["avg_views"].set_value(fmt_int(round(stats.get("avg_views", 0))))
        self._cards["max_views"].set_value(fmt_int(stats.get("max_views", 0)))
        self._cards["posts_per_day"].set_value(str(stats.get("avg_posts_per_day", 0)))
        self._cards["avg_reactions"].set_value(fmt_int(round(stats.get("avg_reactions", 0))))
        self._cards["avg_reposts"].set_value(fmt_int(round(stats.get("avg_reposts", 0))))
        self._cards["max_reposts"].set_value(fmt_int(stats.get("max_reposts", 0)))

        # Same metrics/formulas as compare mode.
        members = info.get("members", 0) or 0
        avg_views = stats.get("avg_views", 0) or 0
        avg_reactions = stats.get("avg_reactions", 0) or 0
        avg_reposts = stats.get("avg_reposts", 0) or 0
        max_views = stats.get("max_views", 0) or 0
        views_ly = stats.get("last_year_views", 0) or 0
        # Older checkpoints predate this stat entirely (None) — fall back to
        # the overall average rather than showing a false 0%.
        avg_views_settled = stats.get("avg_views_settled")
        if avg_views_settled is None:
            avg_views_settled = avg_views
        err_pct = (avg_views_settled / members * 100) if members else 0
        erv_pct = ((avg_reactions + avg_reposts) / avg_views * 100) if avg_views else 0
        virality_index = (max_views / avg_views) if avg_views else 0
        viral_post_share = stats.get("viral_post_share", 0) or 0

        self._cards["err_pct"].set_value(f"{err_pct:.1f}%")
        self._cards["views_last_year"].set_value(short_num(views_ly, 2) if views_ly else "—")
        self._cards["erv_pct"].set_value(f"{erv_pct:.1f}%")
        self._cards["virality_index"].set_value(f"{virality_index:.2f}×")
        self._cards["viral_post_share"].set_value(f"{viral_post_share:.1f}%")

    # ------------------------------------------------------ period wording
    def _unit_word(self, n: int, unit: str) -> str:
        if self.i18n.lang == "ru":
            one, few, many = {
                "year": ("год", "года", "лет"),
                "month": ("месяц", "месяца", "месяцев"),
                "day": ("день", "дня", "дней"),
            }[unit]
            n_abs = abs(n) % 100
            n1 = n_abs % 10
            if 11 <= n_abs <= 14:
                return many
            if n1 == 1:
                return one
            if 2 <= n1 <= 4:
                return few
            return many
        single, plural = {
            "year": ("year", "years"),
            "month": ("month", "months"),
            "day": ("day", "days"),
        }[unit]
        return single if n == 1 else plural

    def _format_span(self, days: int) -> str:
        days = max(int(days or 0), 0)
        years, rem = divmod(days, 365)
        months = rem // 30
        if years == 0 and months == 0:
            return f"{rem} {self._unit_word(rem, 'day')}"
        parts = []
        if years:
            parts.append(f"{years} {self._unit_word(years, 'year')}")
        if months:
            parts.append(f"{months} {self._unit_word(months, 'month')}")
        return " ".join(parts)

    def _period_text(self) -> str:
        days = self._data.get("stats", {}).get("total_days") or 0
        return self._format_span(days) if days else self.tr_("period_all")

    def _fill_charts(self) -> None:
        dist = self._data.get("distributions", {})
        monthly = dist.get("monthly", [])
        m_vals = [m["count"] for m in monthly]
        m_labels = [self._month_label(m["label"]) for m in monthly]
        m_tooltips = [f"{m['label']}: {m['count']}" for m in monthly]
        self.activity_chart.set_data(m_vals, m_labels, tooltips=m_tooltips,
                                     empty_text=self.tr_("chart_empty"))

        hours = dist.get("hour", [0] * 24)
        self.hour_chart.set_data(hours, [str(h) for h in range(24)],
                                 empty_text=self.tr_("chart_empty"))

        wd = dist.get("weekday", [0] * 7)
        wd_labels = [self.tr_(k) for k in
                     ("wd_mon", "wd_tue", "wd_wed", "wd_thu", "wd_fri", "wd_sat", "wd_sun")]
        self.weekday_chart.set_data(wd, wd_labels, empty_text=self.tr_("chart_empty"))

    @staticmethod
    def _month_label(iso_month: str) -> str:
        try:
            y, m = iso_month.split("-")
            return MONTHS_SHORT[int(m)]
        except (ValueError, IndexError):
            return iso_month

    # ------------------------------------------------------- trend chart
    def _on_trend_mode_toggled(self, mode: str, checked: bool) -> None:
        if not checked:
            return
        self._trend_mode = mode
        self._rebuild_trend_chart()

    def _on_trend_series_toggled(self, key: str, checked: bool) -> None:
        self._trend_series_visible[key] = checked
        self._rebuild_trend_chart()

    def _rebuild_trend_chart(self) -> None:
        monthly = self._data.get("distributions", {}).get("monthly") or []
        if self._trend_mode == "season":
            labels, views, reactions, shares = self._trend_season_series(monthly)
        else:
            labels = [self._month_label_full(m.get("label", "")) for m in monthly]
            views = [int(m.get("views", 0) or 0) for m in monthly]
            reactions = [int(m.get("reactions", 0) or 0) for m in monthly]
            shares = [int(m.get("shares", 0) or 0) for m in monthly]
        all_series = {
            "views": {"label": self.tr_("col_views"), "color": COLORS["accent"], "values": views},
            "reactions": {"label": self.tr_("col_reactions"), "color": COLORS["weekday"],
                         "values": reactions},
            "shares": {"label": self.tr_("col_shares"), "color": COLORS["warn"], "values": shares},
        }
        series = [s for key, s in all_series.items() if self._trend_series_visible.get(key)]
        self.trend_chart.set_data(series, labels, empty_text=self.tr_("chart_empty"))

    @staticmethod
    def _trend_season_series(monthly: list[dict]) -> tuple[list[str], list[int], list[int], list[int]]:
        """Sum each season's 3 months together — same season grouping the
        Folder Stats view uses (see app.periods)."""
        buckets: dict[tuple, dict] = {}
        for m in monthly:
            try:
                year, month = (int(x) for x in m.get("label", "").split("-"))
            except ValueError:
                continue
            key, label = period_key_label(year, month, "season")
            b = buckets.setdefault(key, {"label": label, "views": 0, "reactions": 0, "shares": 0})
            b["views"] += int(m.get("views", 0) or 0)
            b["reactions"] += int(m.get("reactions", 0) or 0)
            b["shares"] += int(m.get("shares", 0) or 0)
        keys = sorted(buckets)
        return ([buckets[k]["label"] for k in keys], [buckets[k]["views"] for k in keys],
               [buckets[k]["reactions"] for k in keys], [buckets[k]["shares"] for k in keys])

    @staticmethod
    def _month_label_full(iso_month: str) -> str:
        """Unlike `_month_label` (bar chart, one year's worth of bars at
        most), the trend chart can span years, so a bare "Jul" would be
        ambiguous — tag the year on."""
        try:
            y, m = iso_month.split("-")
            return f"{MONTHS_SHORT[int(m)]} '{y[2:]}"
        except (ValueError, IndexError):
            return iso_month

    # --------------------------------------------------------- table logic
    def _on_header_clicked(self, col: int) -> None:
        if self._SORT_KEYS.get(col) is None:
            return
        if col == self._sort_col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col, self._sort_desc = col, True
        self._rebuild_table()

    def _sort_value(self, row: dict, col: int):
        if col == 5:
            pub = row.get("public")
            return pub["count"] if pub and pub["count"] >= 0 else -1
        return row.get(self._SORT_KEYS[col], 0)

    def _rebuild_table(self) -> None:
        rows = sorted(self._rows,
                      key=lambda r: self._sort_value(r, self._sort_col),
                      reverse=self._sort_desc)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            link = build_post_link(self._channel_text, r["id"])
            self.table.setItem(i, 0, QTableWidgetItem(self._fmt_date(r.get("date", ""))))

            post_text = r.get("text", "") or f"#{r['id']}"
            album_ids = r.get("ids") or [r["id"]]
            if len(album_ids) > 1:
                post_text += self.tr_("album_suffix", n=len(album_ids))
            post_item = QTableWidgetItem(post_text)
            post_item.setToolTip(link)
            post_item.setData(Qt.ItemDataRole.UserRole, link)
            self.table.setItem(i, 1, post_item)

            self.table.setItem(i, 2, QTableWidgetItem(fmt_int(r.get("views", 0))))
            self.table.setItem(i, 3, QTableWidgetItem(fmt_int(r.get("reactions", 0))))
            self.table.setItem(i, 4, QTableWidgetItem(fmt_int(r.get("forwards", 0))))
            self.table.setCellWidget(i, 5, self._public_cell(r))
        order = (Qt.SortOrder.DescendingOrder if self._sort_desc
                 else Qt.SortOrder.AscendingOrder)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSortIndicator(self._sort_col, order)

    # ---------------------------------------------------- top viral table
    def _viral_rate(self, views: int, avg_views: float) -> float:
        return (views / avg_views) if avg_views else 0.0

    def _rebuild_top_viral_table(self) -> None:
        avg_views = self._data.get("stats", {}).get("avg_views", 0) or 0
        rows = sorted(self._rows,
                      key=lambda r: self._viral_rate(r.get("views", 0) or 0, avg_views),
                      reverse=True)[:10]
        self.top_viral_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            link = build_post_link(self._channel_text, r["id"])
            self.top_viral_table.setItem(i, 0, QTableWidgetItem(self._fmt_date(r.get("date", ""))))

            post_text = r.get("text", "") or f"#{r['id']}"
            album_ids = r.get("ids") or [r["id"]]
            if len(album_ids) > 1:
                post_text += self.tr_("album_suffix", n=len(album_ids))
            post_item = QTableWidgetItem(post_text)
            post_item.setToolTip(link)
            post_item.setData(Qt.ItemDataRole.UserRole, link)
            self.top_viral_table.setItem(i, 1, post_item)

            self.top_viral_table.setItem(i, 2, QTableWidgetItem(fmt_int(r.get("views", 0))))
            self.top_viral_table.setItem(i, 3, QTableWidgetItem(fmt_int(r.get("reactions", 0))))
            self.top_viral_table.setItem(i, 4, QTableWidgetItem(fmt_int(r.get("forwards", 0))))
            rate = self._viral_rate(r.get("views", 0) or 0, avg_views)
            rate_text = f"{rate:.2f}×" if avg_views else "—"
            self.top_viral_table.setItem(i, 5, QTableWidgetItem(rate_text))

    def _open_viral_row(self, row: int, _col: int) -> None:
        item = self.top_viral_table.item(row, 1)
        if item:
            link = item.data(Qt.ItemDataRole.UserRole)
            if link:
                QDesktopServices.openUrl(QUrl(link))

    def _public_cell(self, row: dict) -> QWidget:
        cell = QWidget()
        lay = QHBoxLayout(cell)
        lay.setContentsMargins(6, 0, 6, 0)
        pub = row.get("public")
        if pub is None:
            lay.addWidget(QLabel(self.tr_("public_off")))
        elif pub["count"] < 0:
            lay.addWidget(QLabel(self.tr_("public_na")))
        else:
            lay.addWidget(QLabel(fmt_int(pub["count"])), 1)
            if pub["count"] > 0:
                btn = QPushButton(self.tr_("show"))
                btn.setObjectName("ghost")
                btn.clicked.connect(lambda _=False, r=row: self._show_public(r))
                lay.addWidget(btn)
        return cell

    def _show_public(self, row: dict) -> None:
        pub = row.get("public") or {"items": []}
        PublicForwardsDialog(self, self.i18n, row["id"], pub.get("items", [])).exec()

    def _open_row(self, row: int, _col: int) -> None:
        item = self.table.item(row, 1)
        if item:
            link = item.data(Qt.ItemDataRole.UserRole)
            if link:
                QDesktopServices.openUrl(QUrl(link))

    # -------------------------------------------------------- date helpers
    def _fmt_date(self, iso: str) -> str:
        if not iso:
            return ""
        try:
            return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d")
        except ValueError:
            return iso

    def _fmt_datetime(self, iso: str) -> str:
        if not iso:
            return ""
        try:
            cleaned = iso.replace("Z", "+00:00")
            return datetime.fromisoformat(cleaned).astimezone().strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return iso

    # ---------------------------------------------------- header actions
    def _on_refetch(self) -> None:
        if not self._data:
            return
        self.refetch_requested.emit({
            "channel": self._data.get("channel", self._channel_text),
            "top_n": self._data.get("top_n", 20),
            "period": self._data.get("period", ""),
            "fetch_public": self._data.get("fetch_public", False),
        })

    def _on_remove(self) -> None:
        if not self._data:
            return
        if QMessageBox.question(self, self.tr_("app_title"),
                                self.tr_("dash_remove_confirm", name=self._title)) \
                == QMessageBox.StandardButton.Yes:
            self.remove_requested.emit(self._data.get("key", ""))

    # ------------------------------------------------------------- exports
    def _show_report(self) -> None:
        if not self._rows:
            QMessageBox.information(self, self.tr_("app_title"), self.tr_("report_empty"))
            return
        ChannelReportDialog(self, self.i18n, self._build_report_text()).exec()

    def _build_report_text(self) -> str:
        title = self._title or self._channel_text
        lines = [self.tr_("report_title", title=title), ""]
        for key, header_key, emoji in (
            ("forwards", "report_private", "🔄"),
            ("views", "report_views", "👁"),
            ("reactions", "report_reactions", "❤️"),
        ):
            lines.append(self.tr_(header_key, emoji=emoji))
            lines.append("")
            top = sorted(self._rows, key=lambda r: r.get(key, 0), reverse=True)[:7]
            for i, r in enumerate(top, 1):
                link = build_post_link(self._channel_text, r["id"])
                link = link.removeprefix("https://").removeprefix("http://")
                snippet = (r.get("text") or "").strip()
                if len(snippet) > 50:
                    snippet = snippet[:49] + "…"
                if not snippet:
                    snippet = f"#{r['id']}"
                lines.append(f"{i}. {r.get(key, 0)} {emoji} {link} {snippet}")
            lines.append("")
            lines.append("")
        return "\n".join(lines).rstrip("\n") + "\n"

    def _public_md(self, row: dict) -> str:
        pub = row.get("public")
        if pub is None:
            return self.tr_("public_off")
        if pub["count"] < 0:
            return self.tr_("public_na")
        items = pub.get("items") or []
        if not items:
            return str(pub["count"])
        links = []
        for it in items:
            t = " ".join((it.get("title") or "?").split())
            t = t.replace("|", "\\|").replace("[", "(").replace("]", ")")
            link = it.get("link") or ""
            links.append(f"[{t}]({link})" if link else t)
        return f"{pub['count']} " + "<br>".join(links)

    def _save_md(self) -> None:
        if not self._rows:
            QMessageBox.information(self, self.tr_("app_title"), self.tr_("report_empty"))
            return
        default = f"{self._data.get('key', 'channel')}_top.md"
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr_("save_md_button"), default, "Markdown (*.md)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._build_md_table())
        except OSError as exc:
            QMessageBox.warning(self, self.tr_("app_title"), str(exc))
            return
        QMessageBox.information(self, self.tr_("app_title"),
                                self.tr_("md_saved", path=path))

    def _build_md_table(self) -> str:
        title = self._title or self._channel_text
        rows = sorted(self._rows, key=lambda r: self._sort_value(r, self._sort_col),
                      reverse=self._sort_desc)
        lines = [f"# {self.tr_('report_title', title=title)}", ""]
        headers = [self.tr_("col_date"), self.tr_("col_post"), self.tr_("col_views"),
                   self.tr_("col_reactions"), self.tr_("col_private"), self.tr_("col_public")]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for r in rows:
            link = build_post_link(self._channel_text, r["id"])
            text = (r.get("full_text") or r.get("text") or f"#{r['id']}")
            text = text.replace("|", "\\|").replace("\n", " ").strip() or f"#{r['id']}"
            date = self._fmt_datetime(r.get("date", ""))
            lines.append(
                f"| {date} | [{text}]({link}) | {r.get('views', 0)} | "
                f"{r.get('reactions', 0)} | {r.get('forwards', 0)} | "
                f"{self._public_md(r)} |")
        return "\n".join(lines) + "\n"

    # ---------------------------------------------------------- translate
    def retranslate(self) -> None:
        self.folder_btn.setToolTip(self.tr_("folder_choose"))
        self.report_btn.setText(self.tr_("report_button"))
        self.md_btn.setText(self.tr_("save_md_button"))
        self.refetch_btn.setText(self.tr_("dash_refresh"))
        self.remove_btn.setText(self.tr_("dash_remove"))
        self.trend_card.title_lbl.setText(self.tr_("chart_trend_title"))
        self.trend_mode_season_btn.setText(self.tr_("period_mode_season"))
        self.trend_mode_month_btn.setText(self.tr_("period_mode_month"))
        for key, title_key in (("views", "col_views"), ("reactions", "col_reactions"),
                               ("shares", "col_shares")):
            self._trend_series_btns[key].setText(f"● {self.tr_(title_key)}")
        self.activity_card.set_title(self.tr_("chart_activity"))
        self.hour_card.set_title(self.tr_("chart_by_hour"))
        self.weekday_card.set_title(self.tr_("chart_by_weekday"))
        self.table_card.title_lbl.setText(self.tr_("top_posts_title"))
        self.table.setHorizontalHeaderLabels([
            self.tr_("col_date"), self.tr_("col_post"), self.tr_("col_views"),
            self.tr_("col_reactions"), self.tr_("col_private"), self.tr_("col_public")])
        self.top_viral_card.title_lbl.setText(self.tr_("top_viral_title"))
        self.top_viral_table.setHorizontalHeaderLabels([
            self.tr_("col_date"), self.tr_("col_post"), self.tr_("col_views"),
            self.tr_("col_reactions"), self.tr_("col_private"), self.tr_("col_viral_rate")])
        keymap = {"members": "stat_members", "avg_views": "stat_avg_views",
                  "max_views": "stat_max_views", "posts_per_day": "stat_posts_per_day",
                  "avg_reactions": "stat_avg_reactions", "avg_reposts": "stat_avg_reposts",
                  "max_reposts": "stat_max_reposts", "erv_pct": "cmp_erv_pct",
                  "virality_index": "cmp_virality_index", "viral_post_share": "cmp_viral_share"}
        for k, key in keymap.items():
            self._cards[k].title_lbl.setText(self.tr_(key))
        self._cards["total_posts"].title_lbl.setText(
            self.tr_("stat_total_posts_period",
                     period=self._period_text() if self._data else ""))
        self._cards["err_pct"].title_lbl.setText(self._metric_title("err_pct", "stat_err_pct"))
        self._cards["views_last_year"].title_lbl.setText(
            self._metric_title("views_last_year", "cmp_view_repost_year"))
        for key, tip_key in _CARD_TOOLTIPS.items():
            self._cards[key].setToolTip(self.tr_(tip_key))
        if self._data:
            self.sub_lbl.setText(self._header_sub())
        self.refresh_folder_button()
