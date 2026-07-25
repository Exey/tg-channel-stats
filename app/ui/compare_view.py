"""Side-by-side comparison of 2-4 fetched channels.

Reuses the same StatCard tiles as the single-channel dashboard, laid out as
one column per channel so the numbers line up for an easy diff. Selection
happens from the sidebar's compare mode (see SidePanel.compare_requested).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from .dashboard_view import fmt_int
from .widgets import StatCard, hline

_METRICS = [
    ("members", "stat_members"),
    ("avg_views", "stat_avg_views"),
    ("max_views", "stat_max_views"),
    ("posts_per_day", "stat_posts_per_day"),
    ("avg_reactions", "stat_avg_reactions"),
    ("avg_reposts", "stat_avg_reposts"),
    ("max_reposts", "stat_max_reposts"),
    ("views_per_member", "stat_views_per_member"),
    ("reposts_per_post", "stat_reposts_per_post"),
    ("err_pct", "stat_err_pct"),
]

_CARD_HEIGHT = round(132 * 0.6)  # StatCard's default (132) minus 40%
MAX_COMPARE = 4


class CompareView(QWidget):
    def __init__(self, i18n, parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self._build_ui()

    def tr_(self, key: str, **kw) -> str:
        return self.i18n.tr(key, **kw)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(34, 28, 40, 24)
        outer.setSpacing(16)

        self.title_lbl = QLabel(self.tr_("compare_title"))
        self.title_lbl.setObjectName("pageTitle")
        outer.addWidget(self.title_lbl)
        outer.addWidget(hline())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        body = QWidget()
        columns = QHBoxLayout(body)
        columns.setContentsMargins(0, 6, 8, 0)
        columns.setSpacing(18)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self._columns: list[dict] = []
        for _ in range(MAX_COMPARE):
            col_lay = QVBoxLayout()
            col_lay.setSpacing(14)
            name_lbl = QLabel("—")
            name_lbl.setObjectName("sectionTitle")
            col_lay.addWidget(name_lbl)
            cards = {}
            for key, title_key in _METRICS:
                title = self.tr_(title_key)
                if key == "err_pct":
                    title = f"{title} ({self.tr_('stat_err_pct_sub')})"
                card = StatCard(title)
                card.setMinimumHeight(_CARD_HEIGHT)
                cards[key] = card
                col_lay.addWidget(card)
            col_lay.addStretch()
            holder = QWidget()
            holder.setLayout(col_lay)
            holder.setMinimumWidth(220)
            columns.addWidget(holder, 1)
            self._columns.append({"holder": holder, "name": name_lbl, "cards": cards})

    def load(self, datas: list[dict]) -> None:
        datas = datas[:MAX_COMPARE]
        raw: list[dict] = []
        for i, col in enumerate(self._columns):
            if i >= len(datas):
                col["holder"].setVisible(False)
                continue
            col["holder"].setVisible(True)
            data = datas[i]
            info = data.get("info", {})
            stats = data.get("stats", {})
            members = info.get("members", 0) or 0
            avg_views = stats.get("avg_views", 0) or 0
            avg_reposts = stats.get("avg_reposts", 0) or 0
            # Older checkpoints predate this stat entirely (None) — fall back
            # to the overall average rather than showing a false 0%.
            avg_views_settled = stats.get("avg_views_settled")
            if avg_views_settled is None:
                avg_views_settled = avg_views
            vals = {
                "members": members,
                "avg_views": avg_views,
                "max_views": stats.get("max_views", 0) or 0,
                "posts_per_day": stats.get("avg_posts_per_day", 0) or 0,
                "avg_reactions": stats.get("avg_reactions", 0) or 0,
                "avg_reposts": avg_reposts,
                "max_reposts": stats.get("max_reposts", 0) or 0,
                "views_per_member": (avg_views / members) if members else 0,
                "reposts_per_post": avg_reposts,
                "err_pct": (avg_views_settled / members * 100) if members else 0,
            }
            raw.append(vals)
            col["name"].setText(data.get("title") or data.get("channel") or "—")
            col["cards"]["members"].set_value(fmt_int(vals["members"]) if vals["members"] else "—")
            col["cards"]["avg_views"].set_value(fmt_int(round(vals["avg_views"])))
            col["cards"]["max_views"].set_value(fmt_int(vals["max_views"]))
            col["cards"]["posts_per_day"].set_value(str(vals["posts_per_day"]))
            col["cards"]["avg_reactions"].set_value(fmt_int(round(vals["avg_reactions"])))
            col["cards"]["avg_reposts"].set_value(fmt_int(round(vals["avg_reposts"])))
            col["cards"]["max_reposts"].set_value(fmt_int(vals["max_reposts"]))
            col["cards"]["views_per_member"].set_value(f"{vals['views_per_member']:.2f}")
            col["cards"]["reposts_per_post"].set_value(fmt_int(round(vals["reposts_per_post"])))
            col["cards"]["err_pct"].set_value(f"{vals['err_pct']:.1f}%")

        shown = self._columns[:len(raw)]
        for key, _ in _METRICS:
            values = [v[key] for v in raw]
            top = max(values) if values else None
            winner = values.index(top) if top is not None and values.count(top) == 1 else None
            for i, col in enumerate(shown):
                col["cards"][key].set_highlighted(i == winner)
