"""Side-by-side comparison of 2-6 fetched channels.

Reuses the same StatCard tiles as the single-channel dashboard, laid out as
one column per channel so the numbers line up for an easy diff. Selection
happens from the sidebar's compare mode (see SidePanel.compare_requested).
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from .dashboard_view import fmt_int, short_num
from .widgets import StatCard

# (card key, i18n key) — order here is display order top-to-bottom.
_METRICS = [
    ("members", "stat_members"),
    ("avg_views", "stat_avg_views"),
    ("views_per_member", "stat_views_per_member"),
    ("max_views", "cmp_max_views"),
    ("views_last_year", "cmp_view_repost_year"),
    ("posts_per_day", "cmp_posts_per_day"),
    ("reposts_per_post", "stat_reposts_per_post"),
    ("max_reposts", "cmp_max_reposts"),
    ("avg_reactions", "cmp_avg_reactions"),
    ("err_pct", "stat_err_pct"),
    ("virality_index", "cmp_virality_index"),
    ("viral_post_share", "cmp_viral_share"),
]

# card key -> i18n key for an explanatory tooltip (only the two least
# self-explanatory cards need one).
_TOOLTIPS = {
    "virality_index": "cmp_virality_index_tip",
    "viral_post_share": "cmp_viral_share_tip",
}

_CARD_HEIGHT = round(132 * 0.6 * 0.9)  # -40%, then another -10%
MAX_COMPARE = 6
LAST_FULL_YEAR = datetime.now().year - 1


class CompareView(QWidget):
    def __init__(self, i18n, parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
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

    def retranslate(self) -> None:
        for key, title_key in _METRICS:
            title = self._metric_title(key, title_key)
            tip = self.tr_(_TOOLTIPS[key]) if key in _TOOLTIPS else ""
            for col in self._columns:
                col["cards"][key].title_lbl.setText(title)
                col["cards"][key].setToolTip(tip)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(34, 16, 40, 24)
        outer.setSpacing(16)

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
            name_row = QHBoxLayout()
            name_lbl = QLabel("—")
            name_lbl.setObjectName("sectionTitle")
            name_row.addWidget(name_lbl, 1)
            crown_lbl = QLabel("👑")
            crown_lbl.setVisible(False)
            name_row.addWidget(crown_lbl)
            col_lay.addLayout(name_row)
            cards = {}
            for key, title_key in _METRICS:
                title = self._metric_title(key, title_key)
                card = StatCard(title)
                card.setMinimumHeight(_CARD_HEIGHT)
                card.set_compact(True)
                if key in _TOOLTIPS:
                    card.setToolTip(self.tr_(_TOOLTIPS[key]))
                cards[key] = card
                col_lay.addWidget(card)
            col_lay.addStretch()
            holder = QWidget()
            holder.setLayout(col_lay)
            holder.setMinimumWidth(200)
            columns.addWidget(holder, 1)
            self._columns.append({"holder": holder, "name": name_lbl,
                                  "crown": crown_lbl, "cards": cards})

    def load(self, datas: list[dict]) -> None:
        datas = datas[:MAX_COMPARE]
        raw: list[dict] = []
        names: list[str] = []
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
            views_ly = stats.get("last_year_views", 0) or 0
            max_views = stats.get("max_views", 0) or 0
            vals = {
                "members": members,
                "avg_views": avg_views,
                "max_views": max_views,
                "posts_per_day": stats.get("avg_posts_per_day", 0) or 0,
                "avg_reactions": stats.get("avg_reactions", 0) or 0,
                "max_reposts": stats.get("max_reposts", 0) or 0,
                "views_per_member": (avg_views / members) if members else 0,
                "reposts_per_post": avg_reposts,
                "err_pct": (avg_views_settled / members * 100) if members else 0,
                "views_last_year": views_ly,
                "virality_index": (max_views / avg_views) if avg_views else 0,
                "viral_post_share": stats.get("viral_post_share", 0) or 0,
            }
            raw.append(vals)
            name = data.get("title") or data.get("channel") or "—"
            names.append(name)
            col["name"].setText(name)
            col["cards"]["members"].set_value(fmt_int(vals["members"]) if vals["members"] else "—")
            col["cards"]["avg_views"].set_value(fmt_int(round(vals["avg_views"])))
            col["cards"]["max_views"].set_value(fmt_int(vals["max_views"]))
            col["cards"]["posts_per_day"].set_value(str(vals["posts_per_day"]))
            col["cards"]["avg_reactions"].set_value(fmt_int(round(vals["avg_reactions"])))
            col["cards"]["max_reposts"].set_value(fmt_int(vals["max_reposts"]))
            col["cards"]["views_per_member"].set_value(f"{vals['views_per_member']:.2f}")
            col["cards"]["reposts_per_post"].set_value(fmt_int(round(vals["reposts_per_post"])))
            col["cards"]["err_pct"].set_value(f"{vals['err_pct']:.1f}%")
            col["cards"]["views_last_year"].set_value(
                f"{short_num(views_ly, 2)} 👁" if views_ly else "—")
            col["cards"]["virality_index"].set_value(f"{vals['virality_index']:.2f}×")
            col["cards"]["viral_post_share"].set_value(f"{vals['viral_post_share']:.1f}%")

        shown = self._columns[:len(raw)]
        win_counts = [0] * len(shown)
        for key, _ in _METRICS:
            values = [v[key] for v in raw]
            top = max(values) if values else None
            winner = values.index(top) if top is not None and top > 0 and values.count(top) == 1 else None
            for i, col in enumerate(shown):
                is_win = i == winner
                col["cards"][key].set_highlighted(is_win)
                if is_win:
                    win_counts[i] += 1

        best = max(win_counts) if win_counts else 0
        overall = win_counts.index(best) if best > 0 and win_counts.count(best) == 1 else None
        for i, col in enumerate(shown):
            col["name"].setText(names[i])
            col["crown"].setVisible(i == overall)