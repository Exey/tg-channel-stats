"""Multi-channel trend comparison: one line per selected channel (2-8, chosen
via the sidebar's Compare Charts mode — see SidePanel.compare_charts_selected)
on each of three stacked charts, in this order: Views, Shares, Reactions.
Shares a month/season toggle across all three, and the same MultiLineChart
widget the single-channel dashboard trend chart uses.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout,
    QWidget,
)

from ..periods import period_key_label
from .charts import MultiLineChart
from .dashboard_view import MONTHS_SHORT
from .widgets import ChartCard

MAX_CHANNELS = 8

# Per-channel line colors, picked from FOLDER_COLORS but reordered for max
# contrast between neighbors — FOLDER_COLORS' own order clusters similar
# warm hues together first (red/orange/amber/yellow), which is fine for a
# folder-color swatch picker but reads as near-identical lines on a chart.
_SERIES_COLORS = [
    "#3B82F6",  # blue
    "#EF4444",  # red
    "#EAB308",  # yellow
    "#22C55E",  # green
    "#8B5CF6",  # violet
    "#06B6D4",  # cyan
    "#F97316",  # orange
    "#EC4899",  # pink
]

# (metric key, i18n key for its chart title) — order here is display order
# top-to-bottom, per spec: Views, Shares, Reactions.
_METRICS = [("views", "col_views"), ("shares", "col_shares"), ("reactions", "col_reactions")]


def _pretty_label(key_label: str, mode: str) -> str:
    """"YYYY-MM" -> "Mon 'YY" for month mode; season labels are already
    display-ready and pass through unchanged."""
    if mode != "month":
        return key_label
    try:
        y, m = key_label.split("-")
        return f"{MONTHS_SHORT[int(m)]} '{y[2:]}"
    except (ValueError, IndexError):
        return key_label


class CompareChartsView(QWidget):
    def __init__(self, i18n, parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self._datas: list[dict] = []
        self._mode = "month"
        self._build_ui()

    def tr_(self, key: str, **kw) -> str:
        return self.i18n.tr(key, **kw)

    # --------------------------------------------------------------- build
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(34, 28, 40, 24)
        outer.setSpacing(16)

        header = QVBoxLayout()
        header.setSpacing(2)
        self.title_lbl = QLabel(self.tr_("nav_compare_charts"))
        self.title_lbl.setObjectName("pageTitle")
        header.addWidget(self.title_lbl)
        self.sub_lbl = QLabel(self.tr_("compare_charts_sub"))
        self.sub_lbl.setObjectName("pageSub")
        header.addWidget(self.sub_lbl)
        outer.addLayout(header)

        mode_row = QHBoxLayout()
        self.mode_season_btn = QPushButton(self.tr_("period_mode_season"))
        self.mode_season_btn.setObjectName("ghost")
        self.mode_season_btn.setCheckable(True)
        self.mode_month_btn = QPushButton(self.tr_("period_mode_month"))
        self.mode_month_btn.setObjectName("ghost")
        self.mode_month_btn.setCheckable(True)
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_group.addButton(self.mode_season_btn)
        self._mode_group.addButton(self.mode_month_btn)
        mode_row.addWidget(self.mode_season_btn)
        mode_row.addWidget(self.mode_month_btn)
        mode_row.addStretch()
        outer.addLayout(mode_row)

        self.legend_row = QHBoxLayout()
        self.legend_row.setSpacing(14)
        outer.addLayout(self.legend_row)

        self.empty_lbl = QLabel(self.tr_("compare_charts_empty"))
        self.empty_lbl.setObjectName("navEmpty")
        self.empty_lbl.setWordWrap(True)
        outer.addWidget(self.empty_lbl)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(0, 6, 8, 0)
        self.body.setSpacing(20)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self._charts: dict[str, MultiLineChart] = {}
        self._cards: dict[str, ChartCard] = {}
        for key, title_key in _METRICS:
            chart = MultiLineChart(shared_scale=True)  # same metric across channels -> one fair axis
            chart.setMinimumHeight(280)
            card = ChartCard(self.tr_(title_key), chart)
            card.setMinimumHeight(340)
            self.body.addWidget(card)
            self._charts[key] = chart
            self._cards[key] = card

        # Widgets above exist now, so it's safe to wire signals and set the
        # default mode (which fires the toggle once).
        self.mode_season_btn.toggled.connect(lambda checked: self._on_mode_toggled("season", checked))
        self.mode_month_btn.toggled.connect(lambda checked: self._on_mode_toggled("month", checked))
        self.mode_month_btn.setChecked(True)

    # --------------------------------------------------------------- data
    def load(self, datas: list[dict]) -> None:
        self._datas = list(datas[:MAX_CHANNELS])
        self._rebuild_legend()
        self._rebuild_charts()

    def _on_mode_toggled(self, mode: str, checked: bool) -> None:
        if not checked:
            return
        self._mode = mode
        self._rebuild_charts()

    def _clear_legend(self) -> None:
        while self.legend_row.count():
            item = self.legend_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _rebuild_legend(self) -> None:
        self._clear_legend()
        for i, data in enumerate(self._datas):
            color = _SERIES_COLORS[i % len(_SERIES_COLORS)]
            name = data.get("title") or data.get("channel") or "—"
            lbl = QLabel(f"● {name}")
            lbl.setStyleSheet(f"color: {color}; font-weight: 600;")
            self.legend_row.addWidget(lbl)
        self.legend_row.addStretch()

    def _rebuild_charts(self) -> None:
        has_data = bool(self._datas)
        self.empty_lbl.setVisible(not has_data)
        for card in self._cards.values():
            card.setVisible(has_data)
        if not has_data:
            return

        # Each channel's monthly rows, grouped into month/season buckets;
        # also collect the union of period keys so every line spans the same
        # x-axis even if one channel has a shorter fetch history than another.
        all_keys: dict[tuple, str] = {}
        per_channel: list[dict[tuple, dict]] = []
        for data in self._datas:
            monthly = data.get("distributions", {}).get("monthly") or []
            buckets: dict[tuple, dict] = {}
            for m in monthly:
                try:
                    year, month = (int(x) for x in m.get("label", "").split("-"))
                except ValueError:
                    continue
                key, label = period_key_label(year, month, self._mode)
                b = buckets.setdefault(key, {"views": 0, "shares": 0, "reactions": 0})
                b["views"] += int(m.get("views", 0) or 0)
                b["shares"] += int(m.get("shares", 0) or 0)
                b["reactions"] += int(m.get("reactions", 0) or 0)
                all_keys[key] = label
            per_channel.append(buckets)

        keys = sorted(all_keys)
        labels = [_pretty_label(all_keys[k], self._mode) for k in keys]

        for metric, _title_key in _METRICS:
            series = []
            for i, (data, buckets) in enumerate(zip(self._datas, per_channel)):
                name = data.get("title") or data.get("channel") or "—"
                color = _SERIES_COLORS[i % len(_SERIES_COLORS)]
                values = [buckets.get(k, {}).get(metric, 0) for k in keys]
                series.append({"label": name, "color": color, "values": values})
            self._charts[metric].set_data(series, labels, empty_text=self.tr_("chart_empty"))

    # ---------------------------------------------------------- translate
    def retranslate(self) -> None:
        self.title_lbl.setText(self.tr_("nav_compare_charts"))
        self.sub_lbl.setText(self.tr_("compare_charts_sub"))
        self.mode_season_btn.setText(self.tr_("period_mode_season"))
        self.mode_month_btn.setText(self.tr_("period_mode_month"))
        self.empty_lbl.setText(self.tr_("compare_charts_empty"))
        for key, title_key in _METRICS:
            self._cards[key].set_title(self.tr_(title_key))
