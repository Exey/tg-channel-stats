"""Per-channel dashboard: stat cards, activity charts and the top-posts table.

The layout borrows the analytics_dashboard grid (a row of KPI tiles, a wide
activity chart, paired distribution charts) and renders it from a stored
channel checkpoint. The top-posts table — sortable columns, album merging,
public-repost drill-down, Markdown / text export — is the channel_top feature
set, kept intact.
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QFileDialog,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from PySide6.QtCore import Signal

from .charts import BarChart
from .theme import COLORS
from .widgets import Card, ChartCard, SectionCard, StatCard, hline

MONTHS_SHORT = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fmt_int(n) -> str:
    try:
        return f"{int(n):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(n)


def build_post_link(channel_text: str, msg_id: int) -> str:
    """t.me link from whatever channel identifier we have (@user or -100…)."""
    v = str(channel_text).strip().lstrip("@")
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
    def __init__(self, parent, i18n, text: str) -> None:
        super().__init__(parent)
        self._text = text
        self.setWindowTitle(i18n.tr("report_dialog_title"))
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

    # table column index -> row-dict key (None = not sortable)
    _SORT_KEYS = {0: "ts", 2: "views", 3: "reactions", 4: "forwards", 5: "public"}

    def __init__(self, i18n, parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self._data: dict = {}
        self._rows: list[dict] = []
        self._channel_text = ""
        self._title = ""
        self._sort_col = 2
        self._sort_desc = True
        self._build_ui()

    def tr_(self, key: str, **kw) -> str:
        return self.i18n.tr(key, **kw)

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
        ]
        for i, (key, title_key) in enumerate(specs):
            accent = COLORS["accent"] if key != "total_posts" else COLORS["activity"]
            card = StatCard(self.tr_(title_key), accent=accent)
            self._cards[key] = card
            grid.addWidget(card, i // 4, i % 4)
        for col in range(4):
            grid.setColumnStretch(col, 1)
        self.body.addLayout(grid)

    def _build_charts(self) -> None:
        self.activity_chart = BarChart(accent=COLORS["activity"])
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
        self._rebuild_table()

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
        self.report_btn.setText(self.tr_("report_button"))
        self.md_btn.setText(self.tr_("save_md_button"))
        self.refetch_btn.setText(self.tr_("dash_refresh"))
        self.remove_btn.setText(self.tr_("dash_remove"))
        self.activity_card.set_title(self.tr_("chart_activity"))
        self.hour_card.set_title(self.tr_("chart_by_hour"))
        self.weekday_card.set_title(self.tr_("chart_by_weekday"))
        self.table_card.title_lbl.setText(self.tr_("top_posts_title"))
        self.table.setHorizontalHeaderLabels([
            self.tr_("col_date"), self.tr_("col_post"), self.tr_("col_views"),
            self.tr_("col_reactions"), self.tr_("col_private"), self.tr_("col_public")])
        keymap = {"members": "stat_members", "avg_views": "stat_avg_views",
                  "max_views": "stat_max_views", "posts_per_day": "stat_posts_per_day",
                  "avg_reactions": "stat_avg_reactions", "avg_reposts": "stat_avg_reposts",
                  "max_reposts": "stat_max_reposts"}
        for k, key in keymap.items():
            self._cards[k].title_lbl.setText(self.tr_(key))
        self._cards["total_posts"].title_lbl.setText(
            self.tr_("stat_total_posts_period",
                     period=self._period_text() if self._data else ""))
        if self._data:
            self.sub_lbl.setText(self._header_sub())
