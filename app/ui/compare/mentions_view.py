"""Mentions view (nav_mentions): up to MAX_MENTIONS_COMPARE channels' post
texts side by side, so mentions of the same person (a model, a photographer)
across different channels can be spotted and reconciled into one canonical
identity. No page title/header of its own — the sidebar's Mentions button
already says what this is, and the view trades that for more height to work
in (see _build_ui).

Two tiers, one scrollable page:

- Per-channel columns — a period filter (shared across columns, so all four
  are read on the same footing), the channel's post texts in scope as a
  sortable table (date / media type / text, extracted person names
  highlighted inline — see _populate_texts_table), and a staging table of
  the names app.mentions.extract_person_names found: whether each is
  already in mentions.md (and if not, a way to link it there), and the post
  ids it came from (each independently clickable, hover shows the cached
  thumbnail if one's been fetched — see _post_id_chips).
- The mentions.md table itself (app.mentions.MentionsStore) — id / names /
  unclear links, directly editable. Autosaves on leaving the view (hideEvent)
  and via an explicit Save button that's only enabled while there are
  unsaved edits.
"""
from __future__ import annotations

import html
import re
import subprocess
import sys
from datetime import datetime

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMenu, QMessageBox,
    QPushButton, QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ...mentions import (
    MentionsStore, NameExceptions, extract_person_names, extraction_available,
    find_known_names_in_text,
)
from ...media_cache import thumbnail_path
from ...periods import period_key_label
from ...store import ChannelStore
from ..dashboard_view import build_post_link
from ..theme import COLORS
from ..widgets import hline

# media_type -> the label _format_media_type shows, in display order; a
# "Circle" (video_note) never carries a ×N — Telegram round videos can't be
# grouped into an album, so it's always exactly one.
_MEDIA_TYPE_LABELS = [
    ("photo", "Photos"), ("video", "Video"), ("video_note", "Circle"),
    ("audio", "Audio"), ("file", "File"),
]

# Fewer columns than Compare/Compare Charts (MAX_COMPARE=8, see compare_view)
# — four channels' full post texts side by side is already a lot to read.
MAX_MENTIONS_COMPARE = 4
_ALL_TIME = ("all", None)  # period combo sentinel

# The mentions.md table's "names" column is kept at this fraction of the
# table's width (see _resize_mentions_table_columns) — Qt's QHeaderView has
# no native percentage resize mode, only fixed pixels or "stretch to fill
# whatever's left", so this is recomputed by hand on every resize.
_MENTIONS_NAMES_COL_FRACTION = 0.45

# Floor for the mentions.md table's height (~2x names_table's own 260,
# since mentions.md is the persistent canonical store, more prominent than
# the per-column staging tables) — see _fit_mentions_table_height, which
# grows it past this to fit all of its own rows instead of ever scrolling
# internally, so the page's own scrollbar is the only one a viewer has to
# fight with.
_MENTIONS_TABLE_MIN_HEIGHT = 520


def _parse_date(iso: str):
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _link_post_id(channel_text: str, post_id: int) -> str:
    return build_post_link(channel_text, post_id)


def _short_date(iso: str) -> str:
    """"2026-09-01T00:00:00" -> "26-09-01" -- the texts table's Date column
    is narrow enough that the century is dead weight."""
    return (iso or "")[2:10]


def _thumb_tooltip(link: str, fallback: str) -> str:
    """Rich-text tooltip embedding the cached thumbnail for `link`'s post, if
    one's been fetched (High-Quality Posts / dashboard "Fetch media") —
    otherwise just `fallback` as plain text. `link` is a t.me/<channel>/<id>
    (or t.me/c/<id>/<msg>) URL, same shape build_post_link produces."""
    m = re.search(r"t\.me/(c/\d+|[^/]+)/(\d+)$", link)
    if not m:
        return fallback
    channel, post_id = m.group(1), int(m.group(2))
    path = thumbnail_path(channel, post_id)
    if not path.exists():
        return fallback
    return f'<img src="{path}" width="180"><br>{fallback}'


def _format_media_type(post: dict) -> str:
    """"Photos x9", "Video x1", "Circle", "Photos x2, Video x1" for a mixed
    album, or "Text" for a post with no media — from the per-type tally in
    `media_counts` (see channel_stat.py), not just the anchor `media_type`,
    so a mixed album shows every type it actually carries."""
    counts = post.get("media_counts") or {}
    parts = []
    for mt, label in _MEDIA_TYPE_LABELS:
        n = int(counts.get(mt, 0) or 0)
        if not n:
            continue
        parts.append(label if mt == "video_note" else f"{label} x{n}")
    return ", ".join(parts) if parts else "Text"


def _highlight_names(text: str, names: list[str]) -> str:
    """`text`, HTML-escaped, with every occurrence of each of `names` (exact
    surface form, as app.mentions.extract_person_names found it) wrapped in
    a bold accent span — so it's obvious at a glance which words the
    extractor picked out of the sentence around them."""
    out = html.escape(text)
    for name in sorted(set(names), key=len, reverse=True):  # longest first, so a
        needle = html.escape(name)                          # short name inside a
        if not needle:                                      # longer one doesn't
            continue                                         # split its span
        out = re.sub(re.escape(needle),
                     f'<b style="color:#F97316;">{needle}</b>', out)
    return out


class MentionsView(QWidget):

    def __init__(self, i18n, parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.mentions_store = MentionsStore()
        self.name_exceptions = NameExceptions()
        self.channel_store = ChannelStore()
        self._channels: list[dict] = []   # up to MAX_MENTIONS_COMPARE checkpoint dicts
        self._period_options: list[tuple] = []   # [(key, label), ...], _ALL_TIME first
        self._selected_period = _ALL_TIME
        self._loading_table = False   # guard against itemChanged during a programmatic rebuild
        # {numeric Telegram channel_id: ChannelStore.list() summary} -- lets
        # a repost's bare fwd_from channel_id (see
        # channel_stat._repost_source) resolve to a name without any live
        # Telegram API call, by matching it against this app's own tracked
        # channels. Refreshed once per load() (see _repost_source_html).
        self._channel_id_lookup: dict[int, dict] = {}
        self._build_ui()

    def tr_(self, key: str, **kw) -> str:
        return self.i18n.tr(key, **kw)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.table and event.type() == QEvent.Type.Resize:
            # event.size() (this resize's own new size), not a live query
            # like self.table.viewport().width() -- the viewport hasn't
            # necessarily been resized yet at the point this fires (it's a
            # separate child widget QAbstractScrollArea updates on its own),
            # so a live read here lags one resize behind.
            self._resize_mentions_table_columns(event.size().width())
        return super().eventFilter(obj, event)

    def _resize_mentions_table_columns(self, total_width: int | None = None) -> None:
        """Keeps "names" at _MENTIONS_NAMES_COL_FRACTION of self.table's
        width — called on every resize (see eventFilter) since Qt has no
        built-in percentage column-resize mode."""
        total = self.table.viewport().width() if total_width is None else total_width
        if total <= 0:   # not laid out yet (e.g. the QTimer.singleShot(0, ...)
            return        # in _build_ui firing before the first real geometry)
        self.table.setColumnWidth(1, int(total * _MENTIONS_NAMES_COL_FRACTION))

    def _fit_mentions_table_height(self) -> None:
        """Grows self.table to exactly fit its header plus every current
        row (floor: _MENTIONS_TABLE_MIN_HEIGHT) — paired with its vertical
        scrollbar being off (see _build_ui), so it never needs to scroll
        internally; call after anything that can change row count or row
        heights (resizeRowsToContents first, if a row just changed size)."""
        rows_h = sum(self.table.rowHeight(i) for i in range(self.table.rowCount()))
        header_h = self.table.horizontalHeader().height()
        frame = 2 * self.table.frameWidth()
        self.table.setFixedHeight(max(_MENTIONS_TABLE_MIN_HEIGHT, header_h + rows_h + frame + 4))

    # -------------------------------------------------------------- build
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        body = QWidget()
        page = QVBoxLayout(body)
        # No page title/subtitle here (the sidebar button already says what
        # this is) and a tighter top margin than other views — trading that
        # header for more height to actually work in, since four columns of
        # post tables need all the room they can get.
        page.setContentsMargins(34, 14, 40, 24)
        page.setSpacing(12)
        scroll.setWidget(body)

        self.no_extraction_lbl = QLabel(self.tr_("mentions_no_extraction"))
        self.no_extraction_lbl.setObjectName("hint")
        self.no_extraction_lbl.setWordWrap(True)
        self.no_extraction_lbl.setVisible(not extraction_available())
        page.addWidget(self.no_extraction_lbl)

        period_row = QHBoxLayout()
        self.period_lbl = QLabel(self.tr_("mentions_period_label"))
        period_row.addWidget(self.period_lbl)
        self.period_combo = QComboBox()
        self.period_combo.currentIndexChanged.connect(self._on_period_changed)
        period_row.addWidget(self.period_combo)
        period_row.addSpacing(16)

        # Which pairs of the loaded channels talk about the same people —
        # exact-surface-form overlap between their name_hits (see
        # _refresh_similar_mentions), a quick "these two cover the same
        # beat" signal before reading four columns of text. Same row as the
        # period picker; hidden below 2 loaded channels, since there's no
        # pair to compare yet.
        self.similar_mentions_lbl = QLabel("")
        self.similar_mentions_lbl.setObjectName("sectionTitle")
        self.similar_mentions_lbl.setVisible(False)
        period_row.addWidget(self.similar_mentions_lbl)

        period_row.addStretch()
        self.reload_btn = QPushButton(self.tr_("mentions_reload_btn"))
        self.reload_btn.setToolTip(self.tr_("mentions_reload_hint"))
        self.reload_btn.clicked.connect(self._on_reload_clicked)
        period_row.addWidget(self.reload_btn)
        page.addLayout(period_row)

        self.empty_lbl = QLabel(self.tr_("mentions_empty"))
        self.empty_lbl.setObjectName("hint")
        page.addWidget(self.empty_lbl)

        self._columns_row = QHBoxLayout()
        self._columns_row.setSpacing(14)
        page.addLayout(self._columns_row)
        self._columns: list[dict] = []
        for _ in range(MAX_MENTIONS_COMPARE):
            self._columns.append(self._build_column())

        page.addWidget(hline())

        table_header = QHBoxLayout()
        self.table_title = QLabel(self.tr_("mentions_table_title"))
        self.table_title.setObjectName("sectionTitle")
        table_header.addWidget(self.table_title)
        self.locate_btn = QPushButton(self.tr_("mentions_locate_btn"))
        self.locate_btn.setToolTip(self.tr_("mentions_locate_hint"))
        self.locate_btn.setStyleSheet("padding: 1px 8px;")
        self.locate_btn.clicked.connect(self._on_locate_clicked)
        table_header.addWidget(self.locate_btn)
        table_header.addStretch(1)
        self.add_row_btn = QPushButton(self.tr_("mentions_add_row_btn"))
        self.add_row_btn.clicked.connect(self._on_add_row_clicked)
        table_header.addWidget(self.add_row_btn)
        self.save_btn = QPushButton(self.tr_("mentions_save_btn"))
        self.save_btn.clicked.connect(self._on_save_clicked)
        table_header.addWidget(self.save_btn)
        page.addLayout(table_header)

        # "names" is kept at _MENTIONS_NAMES_COL_FRACTION of the table's
        # width by hand (see eventFilter/_resize_mentions_table_columns);
        # column 2 ("unclear links") is Stretch, soaking up whatever's left
        # — id/delete stay a fixed, readable size. Word-wrap plus
        # resizeRowsToContents (see _rebuild_table) means a long
        # comma-separated names/links cell grows the row instead of
        # clipping.
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([
            self.tr_("mentions_col_id"), self.tr_("mentions_col_names"),
            self.tr_("mentions_col_links"), ""])
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setWordWrap(True)
        self.table.setColumnWidth(0, 200)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(3, 32)
        # No internal scrollbar -- the table grows to fit every row instead
        # (see _fit_mentions_table_height), so the page's own QScrollArea is
        # the only scrollbar a viewer ever has to reckon with here.
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.itemChanged.connect(self._on_table_item_changed)
        self.table.installEventFilter(self)
        QTimer.singleShot(0, self._resize_mentions_table_columns)
        page.addWidget(self.table)
        page.addStretch(1)

        self._refresh_period_combo()
        self._rebuild_table()
        self._sync_save_btn()

    def _build_column(self) -> dict:
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setSpacing(8)

        # Channel name *and* its available post range share this one line
        # (see _rebuild_column) — no separate "Parse whole history" control;
        # this just reports what's already in the stored checkpoint.
        name_lbl = QLabel("—")
        name_lbl.setObjectName("sectionTitle")
        name_lbl.setWordWrap(True)
        lay.addWidget(name_lbl)

        # A real, titled "ID" column instead of the default 1/2/3 row-number
        # strip (which showed nothing useful) — the vertical header is
        # switched off so it can't show both at once.
        texts_table = QTableWidget(0, 4)
        texts_table.verticalHeader().setVisible(False)
        texts_table.setHorizontalHeaderLabels([
            self.tr_("mentions_col_post_id"), self.tr_("mentions_col_date"),
            self.tr_("mentions_col_type"), self.tr_("mentions_col_text")])
        texts_table.horizontalHeader().setStretchLastSection(True)
        texts_table.horizontalHeader().setSortIndicatorShown(True)
        texts_table.setEditTriggers(texts_table.EditTrigger.NoEditTriggers)
        texts_table.setColumnWidth(0, 64)
        texts_table.setColumnWidth(1, 92)
        texts_table.setColumnWidth(2, 90)
        texts_table.setWordWrap(True)
        texts_table.setMinimumHeight(240)
        lay.addWidget(texts_table)

        names_title = QLabel(self._names_title_text(0, {}))
        names_title.setObjectName("hint")
        lay.addWidget(names_title)

        # Name gets real room for a full Cyrillic ФИО — double Qt's 100px
        # default column width, which is all "Found"/"Posts" left it before.
        names_table = QTableWidget(0, 3)
        names_table.setHorizontalHeaderLabels([
            self.tr_("mentions_col_name"), self.tr_("mentions_col_found"),
            self.tr_("mentions_col_posts")])
        names_table.horizontalHeader().setStretchLastSection(True)
        names_table.setEditTriggers(names_table.EditTrigger.NoEditTriggers)
        names_table.setColumnWidth(0, 200)
        names_table.setMinimumHeight(260)
        lay.addWidget(names_table)

        self._columns_row.addWidget(holder, 1)
        col = {"holder": holder, "name": name_lbl, "texts_table": texts_table,
              "names_table": names_table, "names_title": names_title, "channel": None,
              "posts": [], "name_hits": {},          # cached between re-sorts
              "texts_sort_col": 1, "texts_sort_desc": True}   # Date, newest first
        texts_table.horizontalHeader().sectionClicked.connect(
            lambda section, c=col: self._on_texts_header_clicked(c, section))
        texts_table.cellDoubleClicked.connect(
            lambda row, _col, c=col: self._on_texts_row_double_clicked(c, row))
        return col

    # ---------------------------------------------------------- translate
    def retranslate(self) -> None:
        self.no_extraction_lbl.setText(self.tr_("mentions_no_extraction"))
        self.period_lbl.setText(self.tr_("mentions_period_label"))
        self.reload_btn.setText(self.tr_("mentions_reload_btn"))
        self.reload_btn.setToolTip(self.tr_("mentions_reload_hint"))
        self.empty_lbl.setText(self.tr_("mentions_empty"))
        self.table_title.setText(self.tr_("mentions_table_title"))
        self.locate_btn.setText(self.tr_("mentions_locate_btn"))
        self.locate_btn.setToolTip(self.tr_("mentions_locate_hint"))
        self.add_row_btn.setText(self.tr_("mentions_add_row_btn"))
        self.save_btn.setText(self.tr_("mentions_save_btn"))
        self.table.setHorizontalHeaderLabels([
            self.tr_("mentions_col_id"), self.tr_("mentions_col_names"),
            self.tr_("mentions_col_links"), ""])
        for col in self._columns:
            col["texts_table"].setHorizontalHeaderLabels([
                self.tr_("mentions_col_post_id"), self.tr_("mentions_col_date"),
                self.tr_("mentions_col_type"), self.tr_("mentions_col_text")])
            col["names_table"].setHorizontalHeaderLabels([
                self.tr_("mentions_col_name"), self.tr_("mentions_col_found"),
                self.tr_("mentions_col_posts")])
            col["names_title"].setText(self._names_title_text(len(col["posts"]), col["name_hits"]))
        self._refresh_similar_mentions()
        self._rebuild_table()

    # -------------------------------------------------------------- load
    def load(self, datas: list[dict]) -> None:
        """datas: up to MAX_MENTIONS_COMPARE checkpoint dicts, from the
        sidebar's Mentions multi-select (SidePanel.compare_mentions_selected).

        MentionsStore was only ever loaded once, at MentionsView construction
        (app startup) — so a channel opened here could show found/not-found
        status against a stale in-memory snapshot even though mentions.md on
        disk has since moved on (e.g. edited earlier in the very same run,
        or by hand outside the app). Re-reading it on every open fixes that;
        skipped while there are unsaved edits so reopening/reselecting
        channels mid-edit can't silently discard them. NameExceptions has no
        such unsaved state to protect (every add() persists immediately), so
        it's always reloaded here — picks up a hand-edited name_exceptions.txt
        the same way.
        """
        self.name_exceptions.load()
        if not self.mentions_store.dirty:
            self.mentions_store.load()
            self._rebuild_table()
            self._sync_save_btn()
        self._channel_id_lookup = {
            c["channel_id"]: c for c in self.channel_store.list() if c.get("channel_id")}
        self._channels = datas[:MAX_MENTIONS_COMPARE]
        self.empty_lbl.setVisible(not self._channels)
        self._refresh_period_combo()
        for i, col in enumerate(self._columns):
            if i < len(self._channels):
                col["holder"].setVisible(True)
                col["channel"] = self._channels[i]
                self._rebuild_column(col)
            else:
                col["holder"].setVisible(False)
                col["channel"] = None
        # _rebuild_column already refreshes this, but only as of *its own*
        # column — if the selection just shrank, the columns that got
        # cleared above (the `else` branch) never trigger a follow-up call,
        # so the last _rebuild_column's refresh would still be counting
        # them. One more pass here, now that every column's `channel` for
        # this load() is final, is what actually gets it right.
        self._refresh_similar_mentions()

    def _refresh_period_combo(self) -> None:
        """"All time" plus every season any loaded channel has posts in,
        newest first — one shared filter (not four independent ones) so all
        selected channels are read over the same window."""
        current = self.period_combo.currentData()
        keys: dict[tuple, str] = {}
        for ch in self._channels:
            for m in ch.get("distributions", {}).get("monthly") or []:
                try:
                    year, month = (int(x) for x in m.get("label", "").split("-"))
                except ValueError:
                    continue
                key, label = period_key_label(year, month, "season")
                keys[key] = label
        self._period_options = [_ALL_TIME] + [
            (k, v) for k, v in sorted(keys.items(), key=lambda kv: kv[0], reverse=True)]
        self.period_combo.blockSignals(True)
        self.period_combo.clear()
        self.period_combo.addItem(self.tr_("mentions_period_all"), _ALL_TIME)
        for key, label in self._period_options[1:]:
            self.period_combo.addItem(label, key)
        idx = self.period_combo.findData(current) if current else 0
        self.period_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.period_combo.blockSignals(False)
        self._selected_period = self.period_combo.currentData() or _ALL_TIME

    def _on_period_changed(self, _index: int) -> None:
        self._selected_period = self.period_combo.currentData() or _ALL_TIME
        for col in self._columns:
            if col["channel"] is not None:
                self._rebuild_column(col)
        self._refresh_similar_mentions()

    # ------------------------------------------------------------ columns
    def _posts_in_scope(self, ch: dict) -> list[dict]:
        # Unlike Rating/Quality scoring, reposts aren't excluded here — a
        # forwarded post's own caption can still mention someone, and its
        # forward-source is itself useful context (see _repost_source_html).
        rows = list(ch.get("rows", []) or [])
        if self._selected_period == _ALL_TIME:
            return rows
        out = []
        for r in rows:
            dt = _parse_date(r.get("date", ""))
            if dt is None:
                continue
            if period_key_label(dt.year, dt.month, "season")[0] == self._selected_period:
                out.append(r)
        return out

    def _rebuild_column(self, col: dict) -> None:
        ch = col["channel"]
        col["name"].setText(self._column_title(ch))

        # Extraction happens once per (channel, period) — re-sorting the
        # texts table (see _on_texts_header_clicked) reuses this instead of
        # re-running NER over every post again.
        #
        # known_candidates backstops the NER model's most common miss (a
        # bare first name in a short, casual sentence) with a plain
        # dictionary scan against names mentions.md already knows — see
        # find_known_names_in_text. It can't discover someone new, only
        # confirm a mention of someone already added.
        known_candidates = [
            n for row in self.mentions_store.rows
            for n in ([row.get("id", "")] + list(row.get("names") or []))
            if n.strip()]
        posts = self._posts_in_scope(ch)
        by_post: list[tuple[dict, list[str]]] = []
        name_hits: dict[str, list[int]] = {}
        name_latest_ts: dict[str, int] = {}
        for post in posts:
            text = (post.get("full_text") or post.get("text") or "").strip()
            names = extract_person_names(text) if text else []
            if text and known_candidates:
                for extra in find_known_names_in_text(text, known_candidates):
                    if extra not in names:
                        names.append(extra)
            names = self.name_exceptions.filter(names)
            by_post.append((post, names))
            ts = int(post.get("ts", 0))
            for extracted in names:
                name_hits.setdefault(extracted, []).append(int(post.get("id", 0)))
                if ts > name_latest_ts.get(extracted, -1):
                    name_latest_ts[extracted] = ts
        col["posts"] = by_post
        col["name_hits"] = name_hits

        self._populate_texts_table(col)

        channel_text = ch.get("channel") or ch.get("username") or ""
        table = col["names_table"]
        table.setRowCount(len(name_hits))
        # Newest-mention-first, matching the texts table's default order —
        # ranked by each name's own most recent hit, not insertion order.
        ordered = sorted(name_hits.items(), key=lambda kv: name_latest_ts[kv[0]], reverse=True)
        for i, (extracted, post_ids) in enumerate(ordered):
            table.setItem(i, 0, QTableWidgetItem(extracted))
            table.setCellWidget(i, 1, self._found_indicator(extracted))
            table.setCellWidget(i, 2, self._post_id_chips(channel_text, post_ids))
        table.resizeRowsToContents()
        col["names_title"].setText(self._names_title_text(len(posts), name_hits))

        self._refresh_similar_mentions()

    def _names_title_text(self, posts_count: int, name_hits: dict) -> str:
        linked = sum(1 for n in name_hits if self.mentions_store.find_row(n) is not None)
        return self.tr_("mentions_names_title_counts",
                        posts=posts_count, total=len(name_hits), linked=linked)

    def _refresh_similar_mentions(self) -> None:
        """"1↔2: 4, 2↔3: 2, 1↔4: 1" — exact-surface-form name overlap between
        every pair of currently loaded channels (1-based column position, not
        channel name, to keep this to one line — see the tooltip for the
        legend), most-overlapping pair first. A quick "these two cover the
        same people" signal before reading four columns of text. Hidden
        below 2 loaded channels, since there's no pair to compare yet."""
        loaded = [(i, col) for i, col in enumerate(self._columns) if col["channel"] is not None]
        if len(loaded) < 2:
            self.similar_mentions_lbl.setVisible(False)
            return
        pairs = []
        for x in range(len(loaded)):
            for y in range(x + 1, len(loaded)):
                i, a = loaded[x]
                j, b = loaded[y]
                shared = set(a["name_hits"]) & set(b["name_hits"])
                pairs.append((i + 1, j + 1, len(shared)))
        pairs.sort(key=lambda t: t[2], reverse=True)
        text = ", ".join(f"{a}↔{b}: {c}" for a, b, c in pairs)
        self.similar_mentions_lbl.setText(f'{self.tr_("mentions_similar_title")}: {text}')
        self.similar_mentions_lbl.setToolTip("  ·  ".join(
            f"{i + 1} = {col['channel'].get('title') or col['channel'].get('channel') or '—'}"
            for i, col in loaded))
        self.similar_mentions_lbl.setVisible(True)

    @staticmethod
    def _column_title(ch: dict) -> str:
        """"Channel Name (posts 2019-08 — 2026-09)" — the full scanned range
        from `stats` (not just the period in view, and not just the stored
        top-N `rows` sample), so the column header itself answers "how much
        history do I actually have for this channel"."""
        name = ch.get("title") or ch.get("channel") or "—"
        stats = ch.get("stats", {})
        first, last = (stats.get("first_post_date") or "")[:7], (stats.get("last_post_date") or "")[:7]
        if not (first and last):
            return name
        return f"{name}  (posts {first} — {last})"

    def _populate_texts_table(self, col: dict) -> None:
        """Renders col["posts"] (already extracted, see _rebuild_column)
        into col["texts_table"], sorted by whichever column header was last
        clicked (col["texts_sort_col"]/"texts_sort_desc" — see
        _on_texts_header_clicked)."""
        sort_col, desc = col["texts_sort_col"], col["texts_sort_desc"]
        key = {
            0: lambda pn: pn[0].get("id", 0),
            1: lambda pn: pn[0].get("ts", 0),
            2: lambda pn: _format_media_type(pn[0]),
            3: lambda pn: (pn[0].get("full_text") or pn[0].get("text") or "").casefold(),
        }[sort_col]
        rows = sorted(col["posts"], key=key, reverse=desc)

        table = col["texts_table"]
        table.horizontalHeader().setSortIndicator(
            sort_col, Qt.SortOrder.DescendingOrder if desc else Qt.SortOrder.AscendingOrder)
        table.setRowCount(len(rows))
        for i, (post, names) in enumerate(rows):
            table.setItem(i, 0, QTableWidgetItem(str(post.get("id", ""))))
            table.setItem(i, 1, QTableWidgetItem(_short_date(post.get("date") or "")))
            table.setCellWidget(i, 2, self._type_cell(post, names))
            text = (post.get("full_text") or post.get("text") or "").strip()
            text_lbl = QLabel(self._repost_source_html(post) + _highlight_names(text, names))
            text_lbl.setTextFormat(Qt.TextFormat.RichText)
            text_lbl.setWordWrap(True)
            table.setCellWidget(i, 3, text_lbl)
        table.resizeRowsToContents()

    def _repost_source_html(self, post: dict) -> str:
        """"Forwarded from <name>" (or just "Forwarded" if no name can be
        resolved), prepended before a repost's own text — green if that
        source is already in mentions.md (the same substring-aware
        MentionsStore.find_row used for the Names Found indicator),
        otherwise muted. "" for a post that isn't a repost at all.

        `post["repost_from_id"]` (see channel_stat._repost_source) is a bare
        numeric Telegram channel id with no name attached — resolved here
        against this app's own tracked channels (self._channel_id_lookup,
        refreshed in load()) rather than any live API call, so an untracked
        source just falls back to its byline (`repost_from_author`, if the
        origin channel signed the post) or the unnamed default."""
        if not post.get("repost"):
            return ""
        info = self._channel_id_lookup.get(post.get("repost_from_id"))
        name = (info and (info.get("title") or info.get("channel"))) or post.get("repost_from_author") or ""
        label = self.tr_("mentions_forwarded_from", name=name) if name else self.tr_("mentions_forwarded")
        found = bool(name) and self.mentions_store.find_row(name) is not None
        color = "#22C55E" if found else COLORS["muted"]
        return f'<b style="color:{color};">{html.escape(label)}</b><br>'

    def _type_cell(self, post: dict, names: list[str]) -> QWidget:
        """Media-type text plus, if this post has any extracted names, a
        second line with a compact Link… button — attaching straight from
        here reuses the exact same confirm/correct + pick-target flow as the
        "Names found" section's own Link button (see _on_link_clicked and
        _on_post_names_menu), just without having to scroll down to it."""
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        lay.addWidget(QLabel(_format_media_type(post)))
        if names:
            btn = QPushButton(self.tr_("mentions_link_btn"))
            btn.setStyleSheet("padding: 0px 6px;")
            if len(names) == 1:
                name = names[0]
                btn.clicked.connect(lambda _=False, n=name, b=btn: self._on_link_clicked(n, b))
            else:
                btn.clicked.connect(lambda _=False, ns=names, b=btn: self._on_post_names_menu(ns, b))
            lay.addWidget(btn)
        return holder

    def _on_post_names_menu(self, names: list[str], anchor: QPushButton) -> None:
        """A post can carry more than one extracted name — let the user
        pick which one the Link… button (see _type_cell) should attach."""
        menu = QMenu(self)
        for n in names:
            act = menu.addAction(n)
            act.triggered.connect(lambda _=False, nm=n, b=anchor: self._on_link_clicked(nm, b))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _on_texts_header_clicked(self, col: dict, section: int) -> None:
        if col["texts_sort_col"] == section:
            col["texts_sort_desc"] = not col["texts_sort_desc"]
        else:
            col["texts_sort_col"] = section
            col["texts_sort_desc"] = False
        self._populate_texts_table(col)

    def _on_texts_row_double_clicked(self, col: dict, row: int) -> None:
        """Opens that row's post in Telegram — the ID column (see
        _populate_texts_table) already carries the post id regardless of
        the table's current sort order, so it's read straight off the
        rendered cell rather than re-deriving it from col["posts"]."""
        ch = col["channel"]
        item = col["texts_table"].item(row, 0)
        if ch is None or item is None or not item.text():
            return
        channel_text = ch.get("channel") or ch.get("username") or ""
        link = _link_post_id(channel_text, int(item.text()))
        QDesktopServices.openUrl(QUrl(link))

    def _found_indicator(self, name: str) -> QWidget:
        row = self.mentions_store.find_row(name)
        holder = QWidget()
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(2, 0, 2, 0)
        if row is not None:
            lbl = QLabel(f"✓ {row['id']}")
            lbl.setStyleSheet("color: #22C55E;")
            lay.addWidget(lbl)
        else:
            btn = QPushButton(self.tr_("mentions_link_btn"))
            btn.setStyleSheet("padding: 1px 6px;")
            btn.clicked.connect(lambda _=False, n=name, b=btn: self._on_link_clicked(n, b))
            lay.addWidget(btn)
            ignore_btn = QPushButton(self.tr_("mentions_ignore_btn"))
            ignore_btn.setStyleSheet("padding: 1px 6px;")
            ignore_btn.setToolTip(self.tr_("mentions_ignore_hint"))
            ignore_btn.clicked.connect(lambda _=False, n=name: self._on_ignore_clicked(n))
            lay.addWidget(ignore_btn)
        lay.addStretch()
        return holder

    def _on_ignore_clicked(self, name: str) -> None:
        """"This isn't a name" — the opposite of Link…, for a bare
        extraction that's plainly wrong (e.g. "Мастер-класс"). Added to
        NameExceptions (declension-aware, so this one entry covers every
        grammatical form — see app.mentions), then every loaded column is
        re-extracted so it disappears from this session immediately, not
        just future ones."""
        self.name_exceptions.add(name)
        for col in self._columns:
            if col["channel"] is not None:
                self._rebuild_column(col)

    def _post_id_chips(self, channel_text: str, post_ids: list[int]) -> QWidget:
        holder = QWidget()
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(6)
        for pid in post_ids:
            link = _link_post_id(channel_text, pid)
            lbl = QLabel(f'<a href="{link}">#{pid}</a>')
            lbl.setToolTip(_thumb_tooltip(link, link))
            lbl.linkActivated.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
            lay.addWidget(lbl)
        lay.addStretch()
        return holder

    def _confirm_name_text(self, name: str) -> str | None:
        """NER models occasionally tag a span that isn't actually a person
        (a capitalized post-title word misread as PER, a name's extraction
        cut off mid-phrase, etc.) — this is the one point before that text
        is written into mentions.md where it can be corrected, or the link
        abandoned outright instead of poisoning the store. None if cancelled
        or left blank."""
        text, ok = QInputDialog.getText(
            self, self.tr_("mentions_confirm_name_title"),
            self.tr_("mentions_confirm_name_prompt"), text=name)
        text = text.strip()
        if not ok or not text:
            return None
        return text

    def _on_link_clicked(self, name: str, anchor: QPushButton) -> None:
        corrected = self._confirm_name_text(name)
        if corrected is None:
            return
        menu = QMenu(self)
        for row in self.mentions_store.rows:
            act = menu.addAction(row["id"])
            act.triggered.connect(lambda _=False, r=row, n=corrected: self._link_name_to_row(n, r))
        if self.mentions_store.rows:
            menu.addSeparator()
        new_act = menu.addAction(self.tr_("mentions_link_new"))
        new_act.triggered.connect(lambda _=False, n=corrected: self._link_name_new(n))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _link_name_to_row(self, name: str, row: dict) -> None:
        self.mentions_store.attach_name(row, name)
        self._after_mentions_edit()

    def _link_name_new(self, name: str) -> None:
        id_, ok = QInputDialog.getText(
            self, self.tr_("mentions_link_new"), self.tr_("mentions_new_id_prompt"), text=name)
        id_ = id_.strip()
        if not ok or not id_:
            return
        self.mentions_store.add_row(id_, names=[name])   # name is already attached
        self._after_mentions_edit()

    def _after_mentions_edit(self) -> None:
        self._rebuild_table()
        self._sync_save_btn()
        for col in self._columns:
            if col["channel"] is not None:
                self._rebuild_column(col)
        self._refresh_similar_mentions()

    # -------------------------------------------------------- master table
    def _rebuild_table(self) -> None:
        self._loading_table = True
        try:
            self.table.setRowCount(len(self.mentions_store.rows))
            for i, row in enumerate(self.mentions_store.rows):
                self.table.setItem(i, 0, QTableWidgetItem(row.get("id", "")))
                self.table.setItem(i, 1, QTableWidgetItem(", ".join(row.get("names") or [])))
                self.table.setItem(i, 2, QTableWidgetItem(", ".join(row.get("links") or [])))
                del_btn = QPushButton("🗑")
                del_btn.setStyleSheet("padding: 1px 4px;")
                del_btn.setToolTip(self.tr_("mentions_delete_row_hint"))
                del_btn.clicked.connect(lambda _=False, r=row: self._on_delete_row_clicked(r))
                self.table.setCellWidget(i, 3, del_btn)
            self.table.resizeRowsToContents()
            self._fit_mentions_table_height()
        finally:
            self._loading_table = False

    def _on_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading_table:
            return
        row_idx, col_idx = item.row(), item.column()
        if row_idx >= len(self.mentions_store.rows):
            return
        row = self.mentions_store.rows[row_idx]
        text = item.text().strip()
        if col_idx == 0:
            if row.get("id", "") != text:
                row["id"] = text
                self.mentions_store.dirty = True
        elif col_idx == 1:
            names = [n.strip() for n in text.split(",") if n.strip()]
            if row.get("names") != names:
                row["names"] = names
                self.mentions_store.dirty = True
        elif col_idx == 2:
            links = [link.strip() for link in text.split(",") if link.strip()]
            if row.get("links") != links:
                row["links"] = links
                self.mentions_store.dirty = True
        self.table.resizeRowsToContents()
        self._fit_mentions_table_height()
        self._sync_save_btn()

    def _on_add_row_clicked(self) -> None:
        self.mentions_store.add_row("")
        self._rebuild_table()
        self._sync_save_btn()
        self.table.setCurrentCell(len(self.mentions_store.rows) - 1, 0)
        self.table.editItem(self.table.item(len(self.mentions_store.rows) - 1, 0))

    def _on_locate_clicked(self) -> None:
        """Reveal mentions.md in the OS file manager, selected where that's
        supported (macOS/Windows) rather than just opening its folder."""
        path = self.mentions_store.path
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", str(path)], check=False)
        elif sys.platform.startswith("win"):
            subprocess.run(["explorer", f"/select,{path}"], check=False)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def _on_reload_clicked(self) -> None:
        """Explicit re-read of mentions.md from disk — the same reload
        `load()` already does automatically on opening channels (see its
        docstring), but on demand, e.g. after hand-editing the file while
        this view is already open. Confirms first if there are unsaved
        edits, since a reload discards them."""
        if self.mentions_store.dirty:
            reply = QMessageBox.question(
                self, self.tr_("app_title"), self.tr_("mentions_reload_confirm"))
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.mentions_store.load()
        self._after_mentions_edit()

    def _on_delete_row_clicked(self, row: dict) -> None:
        reply = QMessageBox.question(
            self, self.tr_("app_title"),
            self.tr_("mentions_delete_row_confirm", id=row.get("id", "")))
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.mentions_store.remove_row(row)
        self._after_mentions_edit()

    def _sync_save_btn(self) -> None:
        self.save_btn.setEnabled(self.mentions_store.dirty)

    def _on_save_clicked(self) -> None:
        self.mentions_store.save()
        self._sync_save_btn()

    # ------------------------------------------------------------- events
    def hideEvent(self, event) -> None:
        # Autosave leaving the view — see MentionsStore.save (no-op if clean).
        self.mentions_store.save()
        self._sync_save_btn()
        super().hideEvent(event)

__all__ = ["MAX_MENTIONS_COMPARE", "MentionsView"]
