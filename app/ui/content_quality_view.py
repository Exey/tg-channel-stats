"""High-Quality Posts view: for one folder and period, a grid of individual
*posts* (not channels) ranked by content quality — proportional engagement
relative to that post's own views, not raw view count. Click a card to
open the post on Telegram.

    comments      = min(comments, 100)   # capped — see below
    reaction_wt   = min(reactions, 1000) × 0.045
                  + max(0, min(reactions, 10000) − 1000) × 0.005
    viral_excess  = max(0, views − channel's avg_views)
    ERV% = (forwards × 1.0 + comments × 0.25 + reaction_wt
            + viral_excess × 0.2) / views × 100
    raw  = ERV% × 100
    gauge = raw / (raw + K) × 100 → onto the 0-1000 gauge, K=580 (this
            app's real per-post median raw score) so a typical post lands
            near the middle — a hard clamp would flatten most real posts
            at the ceiling (see the equivalent problem worked through for
            the old channel-level score).

Numerator terms are weighted by how much each actually signals quality,
most to least: forwards (a deliberate, costly share) first, then comments
(real engagement, but cheaper to leave than a share, and capped at 100 —
past that a post is clearly getting real discussion either way, and an
outlier discussion thread of 500+ comments shouldn't just keep dragging the
score up further), then reactions weighted lowest and in two brackets
instead of one flat rate — the first 1000 reactions count at 0.045 each,
anything from there up to 10 000 counts at only 0.005 each, and beyond
10 000 nothing more is added at all (some posts have anomalously high
reaction counts relative to their own views — Telegram's view count can lag
behind reactions, or reactions can accumulate from contexts views don't
capture — so a flat weight would let them dominate the score more than they
should; tapering in brackets keeps a post's first reactions meaningful
without letting a runaway count keep adding weight forever). A post that
beat its own channel's average views also earns a
"viral excess" bonus (floored at 0, so an under-average post gets neither
bonus nor penalty) — weighted at 0.2, but since it's still divided by the
post's own views afterward, this term alone can only ever contribute 0-20%
of ERV%, so a breakout post gets rewarded without swamping the engagement
terms above. Views themselves stay the ratio's denominator throughout, not
a directly-weighted term. This is still a plain per-post ratio, so it
doesn't reward a post just for being the most-viewed one on raw terms — the
viral-excess bonus only rewards *beating its own channel's usual reach*,
which is a different thing (a channel's biggest post is often just the one
that happened to reach the widest audience, not necessarily the best
content — but a post that's unusually large *for that channel* really is
signal). An earlier version of this view scored whole *channels* using a
channel-level "Virality Index" (max_views / avg_views, trimmed/capped to
tame single-post outliers) that was dropped once this view switched to
scoring individual posts — the viral-excess term above is a deliberate,
narrower reintroduction of that same avg_views comparison at the per-post
level, not a return to scoring whole channels.

Posts come from each channel's checkpoint `rows` — the stored top-N sample
(see folder_stat_view's module docstring), not a fresh fetch — filtered to
the selected period (Seasonal/Monthly/All time, same picker as Folder
Stats, backed by app.periods) and capped at MAX_POSTS_SHOWN to keep the
grid manageable for an "All time" view across a big folder.

Thumbnails are opt-in: nothing in this app downloads post media by default
(checkpoints only ever store text/counts), so the header's "Fetch media"
button runs a small on-demand background job (app.tools.media_fetch) that
downloads just the *smallest* thumbnail (photo, video, or round/circle
video note) for the posts currently on screen into a local cache
(app.media_cache). A card without a cached thumbnail shows a placeholder
icon by media type instead (🏞️ photo, ▶️ video, ⚪️ circle message, or
nothing for a text-only post). Both `media_type` and `comments` are new
per-row checkpoint fields (see channel_stat.py) — posts from a checkpoint
fetched before they existed just show no placeholder icon and 0 comments
until refetched.
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices, QFont, QFontMetrics, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..config import Config
from ..folders import FolderStore
from ..media_cache import thumbnail_path
from ..periods import period_key_label
from ..store import ChannelStore
from ..tools.media_fetch import run_thumbnail_cache
from ..worker import ToolWorker
from .charts import GaugeDial
from .dashboard_view import ChannelReportDialog, build_post_link, fmt_int
from .widgets import Card, hline

CARD_WIDTH = 195    # 130 * 1.5
CARD_HEIGHT = 191   # 225 * 0.85
GAUGE_MAX = 1000
_GRID_SPACING = 14
_PAGE_MARGIN_LEFT = 34
_PAGE_MARGIN_RIGHT = 40
_THUMB_HEIGHT = 63   # 74 * 0.85
_GAUGE_SIZE = 49     # 58 * 0.85
MAX_POSTS_SHOWN = 60   # keeps "All time" across a big folder from being huge
MONTHS_FULL = ["", "January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]

_TEXT_LINES = 2
_TEXT_PIXEL_SIZE = 10
_TEXT_WIDTH = CARD_WIDTH - 20   # card's own left+right content margins

_MEDIA_LOG_WIDTH = 260
_MEDIA_LOG_PIXEL_SIZE = 12   # matches QLabel#hint's font-size in theme.py

_PLACEHOLDER_PIXEL_SIZE = 52   # 🏞️/▶️/⚪️ font-size on a card with no cached thumb

# media_type (see channel_stat.py) -> placeholder icon shown until a real
# thumbnail is fetched; "" (text-only post, or an older checkpoint that
# predates this field) shows no icon at all.
_MEDIA_PLACEHOLDERS = {"photo": "🏞️", "video": "▶️", "video_note": "⚪️"}


def _elide_to_lines(text: str, width: int, max_lines: int, pixel_size: int) -> str:
    """Word-wrap `text` to at most `max_lines` lines that fit `width` px at
    `pixel_size`, appending "…" if it had to cut content short — QLabel's
    own elideMode only elides a *single* line, it can't do this after N
    wrapped lines, so this measures and breaks the text by hand.

    Builds a fresh QFont for measurement (rather than trusting a live
    widget's .font()) because this app's global QSS sets font-size in px,
    which makes an unpolished widget's QFont report no usable point/pixel
    size yet — same issue worked through for the Content Quality Index
    metrics line."""
    text = " ".join(text.split())
    if not text:
        return ""
    font = QFont()
    font.setPixelSize(pixel_size)
    fm = QFontMetrics(font)

    words = text.split(" ")
    lines: list[str] = []
    i = 0
    while i < len(words) and len(lines) < max_lines:
        line = words[i]
        i += 1
        while i < len(words):
            trial = f"{line} {words[i]}"
            if fm.horizontalAdvance(trial) > width:
                break
            line = trial
            i += 1
        lines.append(line)

    if i < len(words):  # ran out of lines before running out of words
        last = lines[-1] if lines else ""
        while last and fm.horizontalAdvance(last + "…") > width:
            last = last[:-1].rstrip()
        lines[-1] = f"{last}…" if last else "…"
    return "\n".join(lines)


def _parse_date(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def _channel_label(ch: dict) -> str:
    username = ch.get("username") or ""
    if username:
        return f"@{username}"
    return ch.get("title") or ch.get("channel") or ch.get("key", "?")


def _channel_ref(ch: dict) -> str:
    """Identifier to resolve/link this channel — same fallback order
    build_post_link and the media-fetch tool use."""
    return ch.get("channel") or ch.get("username") or ch.get("key", "")


def _channel_avg_views(ch: dict) -> float:
    """This channel's average views (checkpoint `stats.avg_views`, computed
    by channel_stat.py over its whole scanned history) — 0 for a checkpoint
    that predates that field or never settled a value."""
    return float(ch.get("stats", {}).get("avg_views", 0) or 0)


# Numerator terms are weighted by how much each actually signals content
# quality, most to least: a share (forward) is a deliberate, costly
# endorsement, so it counts most; a comment is real engagement but cheaper
# to leave than a share; a reaction is weighted lowest — see the module
# docstring for why (some posts have anomalously high reaction counts
# relative to their own views). Views stay the ratio's *denominator*, not a
# weighted term — every term above is already measured relative to them.
_FORWARD_WEIGHT = 1.0
_COMMENT_WEIGHT = 0.25
_COMMENT_CAP = 100   # comments beyond this count the same as exactly 100

# Reactions are weighted in two brackets instead of one flat rate, so a
# post's first 1000 reactions still count meaningfully but each one beyond
# that (up to 10 000) counts for much less — and beyond 10 000, nothing
# more is added at all. Same anomalous-reaction-count problem as the flat
# weight this replaced (see module docstring), just tapered smoothly
# instead of crushed uniformly.
_REACTION_TIER1_CAP = 1000
_REACTION_TIER1_WEIGHT = 0.045
_REACTION_TIER2_CAP = 10_000
_REACTION_TIER2_WEIGHT = 0.005


def _reaction_weighted(reactions: int) -> float:
    tier1 = min(reactions, _REACTION_TIER1_CAP) * _REACTION_TIER1_WEIGHT
    tier2 = max(0, min(reactions, _REACTION_TIER2_CAP) - _REACTION_TIER1_CAP) * _REACTION_TIER2_WEIGHT
    return tier1 + tier2

# A post that pulled in more views than its own channel's average is
# rewarded for that too — "views bigger than average" (floored at 0 so an
# under-average post gets no bonus/penalty either way), weighted and folded
# into the same numerator as the terms above. Because it's still divided by
# the post's own views afterward, this term alone can only ever contribute
# 0-20% of ERV% (it approaches, but never reaches, _VIRAL_WEIGHT × 100% as
# views grows far past average) — it boosts a genuine breakout post without
# letting it swamp the engagement-ratio terms.
_VIRAL_WEIGHT = 0.2


def post_score_raw(row: dict, avg_views: float) -> float:
    """ERV% × 100 for one post — see module docstring. `avg_views` is that
    post's own channel's average (checkpoint `stats.avg_views`)."""
    views = int(row.get("views", 0) or 0)
    if not views:
        return 0.0
    reactions = int(row.get("reactions", 0) or 0)
    forwards = int(row.get("forwards", 0) or 0)
    comments = min(int(row.get("comments", 0) or 0), _COMMENT_CAP)
    viral_excess = max(0.0, views - avg_views)
    weighted = (forwards * _FORWARD_WEIGHT + comments * _COMMENT_WEIGHT
               + _reaction_weighted(reactions) + viral_excess * _VIRAL_WEIGHT)
    erv_pct = weighted / views * 100
    return erv_pct * 100


# Real median raw post score across this app's checkpoints (post the
# forward/comment/reaction/viral weighting above) — chosen so a typical
# post lands near the middle of the gauge.
_POST_GAUGE_K = 580.0


def _saturate(raw: float, k: float) -> float:
    """Map an unbounded-above non-negative value onto [0, 100) via
    raw/(raw+k) — see the module docstring for why a hard clamp is worse."""
    if raw <= 0:
        return 0.0
    return 100.0 * raw / (raw + k)


def post_gauge_value(raw_score: float) -> float:
    """Map an unbounded raw post score onto 0-1000 for the gauge."""
    return GAUGE_MAX / 100.0 * _saturate(raw_score, _POST_GAUGE_K)


class _PostCard(Card):
    """A single post: title, thumbnail (if cached), a 2-line text preview,
    and a gauge + absolute counts row. The whole card opens the post on
    click — every child is mouse-transparent so nothing (least of all the
    otherwise-interactive-looking GaugeDial) can swallow that click."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._link = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 9, 10, 7)
        lay.setSpacing(5)

        self.name_lbl = QLabel("—")
        self.name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_lbl.setWordWrap(False)
        self.name_lbl.setObjectName("hint")
        self.name_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(self.name_lbl)

        # Placeholder emoji (🏞️/▶️/⚪️) when there's no cached thumbnail yet —
        # setStyleSheet, not setFont(): theme.py's global `QWidget
        # {font-size: 14px}` rule outranks a plain setFont() once this
        # widget is polished/shown (see the equivalent problem worked
        # through for the Content Quality Index cards' metrics line).
        self.thumb_lbl = QLabel()
        self.thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_lbl.setFixedHeight(_THUMB_HEIGHT)
        self.thumb_lbl.setStyleSheet(f"font-size: {_PLACEHOLDER_PIXEL_SIZE}px;")
        self.thumb_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(self.thumb_lbl)

        # Text is pre-wrapped to exactly 2 lines with a trailing "…" by
        # _elide_to_lines (QLabel can't natively elide after N *wrapped*
        # lines, only a single line) — wordWrap off since the line breaks
        # are already embedded in the string.
        self.text_lbl = QLabel("")
        self.text_lbl.setWordWrap(False)
        self.text_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text_lbl.setObjectName("hint")
        self.text_lbl.setStyleSheet("font-size: 10px;")
        self.text_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(self.text_lbl)

        lay.addStretch(1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.gauge = GaugeDial(0, GAUGE_MAX)
        self.gauge.setFixedSize(_GAUGE_SIZE, _GAUGE_SIZE)
        self.gauge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        bottom.addWidget(self.gauge)

        self.counts_lbl = QLabel("")
        self.counts_lbl.setWordWrap(True)
        self.counts_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        bottom.addWidget(self.counts_lbl, 1)
        lay.addLayout(bottom)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if self._link:
            QDesktopServices.openUrl(QUrl(self._link))
        super().mousePressEvent(event)

    def set_data(self, label: str, thumb: QPixmap | None, placeholder: str, text: str,
                gauge_value: float, counts_text: str, link: str, tooltip: str) -> None:
        self.name_lbl.setText(label)
        if thumb is not None and not thumb.isNull():
            self.thumb_lbl.setPixmap(thumb)
        elif placeholder:
            self.thumb_lbl.setText(placeholder)
        else:
            self.thumb_lbl.clear()
        self.text_lbl.setText(text)
        self.gauge.setValue(max(0, min(GAUGE_MAX, round(gauge_value))))
        self.counts_lbl.setText(counts_text)
        self._link = link
        self.setToolTip(tooltip)


class ContentQualityView(QWidget):
    def __init__(self, i18n, folder_store: FolderStore, channel_store: ChannelStore,
                 cfg: Config, parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.folder_store = folder_store
        self.channel_store = channel_store
        self.cfg = cfg
        self._channels: list[dict] = []
        self._post_entries: list[dict] = []
        self._cards: list[_PostCard] = []
        self._cols = 0
        self._period_mode = "season"
        self._selected_period_key: tuple | None = None
        self._period_btns: dict[tuple, QPushButton] = {}
        self._media_worker: ToolWorker | None = None
        self._build_ui()

    def tr_(self, key: str, **kw) -> str:
        return self.i18n.tr(key, **kw)

    # --------------------------------------------------------------- build
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # The whole page — header, pickers and the card grid — scrolls as
        # one unit (rather than a fixed header above a separately-scrolling
        # grid), so the header/pickers scroll out of the way too instead of
        # permanently eating vertical space.
        self.page_scroll = QScrollArea()
        self.page_scroll.setWidgetResizable(True)
        self.page_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        outer.addWidget(self.page_scroll)

        page = QVBoxLayout(body)
        page.setContentsMargins(_PAGE_MARGIN_LEFT, 28, _PAGE_MARGIN_RIGHT, 24)
        page.setSpacing(16)

        header = QHBoxLayout()
        self.title_lbl = QLabel(self.tr_("nav_content_quality"))
        self.title_lbl.setObjectName("pageTitle")
        header.addWidget(self.title_lbl)
        header.addStretch(1)
        # Last message from the running (or just-finished) media fetch —
        # mainly so a FloodWait cooldown ("FloodWait: sleeping 30s…", see
        # app.tools.common.retry) is visible instead of the button just
        # looking stuck.
        self.media_log_lbl = QLabel("")
        self.media_log_lbl.setObjectName("hint")
        self.media_log_lbl.setFixedWidth(_MEDIA_LOG_WIDTH)
        header.addWidget(self.media_log_lbl)
        self.fetch_media_btn = QPushButton(self.tr_("cqi_fetch_media"))
        self.fetch_media_btn.setToolTip(self.tr_("cqi_fetch_media_hint"))
        self.fetch_media_btn.clicked.connect(self._on_fetch_media_clicked)
        header.addWidget(self.fetch_media_btn)
        self.tg_links_btn = QPushButton(self.tr_("cqi_tg_links"))
        self.tg_links_btn.setToolTip(self.tr_("cqi_tg_links_hint"))
        self.tg_links_btn.clicked.connect(self._on_tg_links_clicked)
        header.addWidget(self.tg_links_btn)
        # Caps how many posts from the same channel can appear — both in
        # the grid below and in the generated Tg Links list, so one
        # prolific channel can't fill up either one. Connected to
        # _on_channel_limit_changed further down, once the grid it needs
        # to rebuild actually exists.
        self.tg_links_limit_combo = QComboBox()
        self.tg_links_limit_combo.setToolTip(self.tr_("cqi_tg_links_limit_hint"))
        self.tg_links_limit_combo.addItem(self.tr_("cqi_tg_links_limit_none"), 0)
        for n in (7, 6, 5, 4, 3, 2):
            self.tg_links_limit_combo.addItem(self.tr_("cqi_tg_links_limit_n", n=n), n)
        header.addWidget(self.tg_links_limit_combo)
        page.addLayout(header)

        pick_row = QHBoxLayout()
        self.pick_lbl = QLabel(self.tr_("folder_stat_pick_folder"))
        pick_row.addWidget(self.pick_lbl)
        self.folder_combo = QComboBox()
        self.folder_combo.currentIndexChanged.connect(self._on_folder_changed)
        pick_row.addWidget(self.folder_combo, 1)
        page.addLayout(pick_row)

        self.mode_season_btn = QPushButton(self.tr_("period_mode_season"))
        self.mode_season_btn.setObjectName("ghost")
        self.mode_season_btn.setCheckable(True)
        self.mode_month_btn = QPushButton(self.tr_("period_mode_month"))
        self.mode_month_btn.setObjectName("ghost")
        self.mode_month_btn.setCheckable(True)
        self.mode_all_btn = QPushButton(self.tr_("period_mode_all"))
        self.mode_all_btn.setObjectName("ghost")
        self.mode_all_btn.setCheckable(True)
        self._mode_btn_group = QButtonGroup(self)
        self._mode_btn_group.setExclusive(True)
        self._mode_btn_group.addButton(self.mode_season_btn)
        self._mode_btn_group.addButton(self.mode_month_btn)
        self._mode_btn_group.addButton(self.mode_all_btn)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.mode_season_btn)
        mode_row.addWidget(self.mode_month_btn)
        mode_row.addWidget(self.mode_all_btn)
        mode_row.addStretch()
        page.addLayout(mode_row)

        self.picker_container = QWidget()
        self.picker_lay = QVBoxLayout(self.picker_container)
        self.picker_lay.setContentsMargins(0, 0, 0, 0)
        self.picker_lay.setSpacing(8)
        page.addWidget(self.picker_container)
        page.addWidget(hline())

        self.no_folders_lbl = QLabel(self.tr_("folder_stat_no_folders"))
        self.no_folders_lbl.setObjectName("navEmpty")
        self.no_folders_lbl.setWordWrap(True)
        page.addWidget(self.no_folders_lbl)

        self.empty_channels_lbl = QLabel(self.tr_("folder_stat_empty_channels"))
        self.empty_channels_lbl.setObjectName("navEmpty")
        self.empty_channels_lbl.setWordWrap(True)
        page.addWidget(self.empty_channels_lbl)

        self.empty_posts_lbl = QLabel(self.tr_("cqi_empty_posts"))
        self.empty_posts_lbl.setObjectName("navEmpty")
        self.empty_posts_lbl.setWordWrap(True)
        page.addWidget(self.empty_posts_lbl)

        self.grid_holder = QWidget()
        self.grid = QGridLayout(self.grid_holder)
        self.grid.setSpacing(_GRID_SPACING)
        self.grid.setContentsMargins(0, 6, 8, 6)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        page.addWidget(self.grid_holder, 1)

        self.page_scroll.setWidget(body)

        # Widgets above exist now, so it's safe to wire signals and set the
        # default mode (which fires the toggle once).
        self.mode_season_btn.toggled.connect(lambda c: self._on_mode_toggled("season", c))
        self.mode_month_btn.toggled.connect(lambda c: self._on_mode_toggled("month", c))
        self.mode_all_btn.toggled.connect(lambda c: self._on_mode_toggled("all", c))
        self.mode_season_btn.setChecked(True)
        self.tg_links_limit_combo.currentIndexChanged.connect(self._on_channel_limit_changed)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        self._relayout_grid()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        # main_window.py calls refresh() *before* stack.setCurrentWidget(),
        # so the very first grid layout can be computed while this view
        # isn't the visible stack page yet and its viewport hasn't settled
        # to its final width — re-check the instant it's actually shown,
        # regardless of what its width looked like when refresh() ran.
        super().showEvent(event)
        self._relayout_grid()

    # --------------------------------------------------------------- data
    def refresh(self) -> None:
        """Reload folders + this folder's channels from disk. Call whenever
        the view is shown, or folders/channels changed elsewhere."""
        current_id = self.folder_combo.currentData()
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        for folder in self.folder_store.list_folders():
            self.folder_combo.addItem(folder["name"], folder["id"])
        if self.folder_combo.count():
            idx = self.folder_combo.findData(current_id) if current_id else -1
            self.folder_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.folder_combo.blockSignals(False)
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

        self._rebuild_period_picker()

    # ------------------------------------------------------- period picker
    @staticmethod
    def _clear_layout(layout) -> None:
        # hide() first — takeAt() only unmanages a widget from the layout,
        # it doesn't hide it, and deleteLater() doesn't actually destroy it
        # until the next event-loop pass. The constructor's initial
        # setChecked(True) and the first refresh() from main_window both
        # rebuild this picker before either yields back to the event loop,
        # so without hide() the old buttons stay visibly stacked under the
        # new ones for a frame.
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                ContentQualityView._clear_layout(sub)

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

    def _collect_all_period_keys(self, mode: str) -> list[tuple[tuple, str]]:
        """Every period key/label across *all* tracked channels, regardless
        of folder — matches Folder Stats: the picker buttons stay put as
        you switch folders, only the posts below change."""
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
                key, label = period_key_label(year, month, mode)
                keys[key] = label
        return sorted(keys.items(), key=lambda kv: kv[0], reverse=True)

    def _rebuild_period_picker(self) -> None:
        self._clear_layout(self.picker_lay)
        self._period_btn_group = QButtonGroup(self)
        self._period_btn_group.setExclusive(True)
        self._period_btns = {}

        if self._period_mode == "all":
            self.picker_container.setVisible(False)
            self._selected_period_key = ("all",)
            self._rebuild_posts()
            return
        self.picker_container.setVisible(True)

        keys_labels = self._collect_all_period_keys(self._period_mode)
        if self._period_mode == "season":
            self._build_season_picker(keys_labels)
        else:
            self._build_month_picker(keys_labels)

        if keys_labels:
            target = (self._selected_period_key if self._selected_period_key in self._period_btns
                      else keys_labels[0][0])
            self._period_btns[target].setChecked(True)
        else:
            self._selected_period_key = None
            self._rebuild_posts()

    def _on_period_button_toggled(self, key: tuple, checked: bool) -> None:
        if not checked:
            return
        self._selected_period_key = key
        self._rebuild_posts()

    # ----------------------------------------------------------- posts
    def _collect_posts(self) -> list[dict]:
        """Every stored row across the selected folder's channels that
        falls in the selected period, each scored — sorted best-first,
        capped at MAX_POSTS_SHOWN."""
        mode = self._period_mode
        target_key = self._selected_period_key
        posts: list[dict] = []
        for ch in self._channels:
            for row in ch.get("rows", []) or []:
                if mode != "all":
                    dt = _parse_date(row.get("date", ""))
                    if dt is None:
                        continue
                    key, _label = period_key_label(dt.year, dt.month, mode)
                    if key != target_key:
                        continue
                posts.append({"channel": ch, "row": row,
                             "raw_score": post_score_raw(row, _channel_avg_views(ch))})
        posts.sort(key=lambda p: p["raw_score"], reverse=True)
        return posts[:MAX_POSTS_SHOWN]

    def _rebuild_posts(self) -> None:
        self._post_entries = self._collect_posts() if self._channels else []
        self._rebuild_cards()

    def _channel_limited_entries(self) -> list[dict]:
        """`self._post_entries` (already best-first) capped to at most N
        posts per channel, N = the Tg Links limit combo's current value (0
        = no cap) — shared by the grid and the generated Tg Links list so
        both always agree on which posts are "in scope"."""
        limit = int(self.tg_links_limit_combo.currentData() or 0)
        if not limit:
            return self._post_entries
        seen: dict[str, int] = {}
        out: list[dict] = []
        for entry in self._post_entries:
            key = _channel_ref(entry["channel"])
            count = seen.get(key, 0)
            if count >= limit:
                continue
            seen[key] = count + 1
            out.append(entry)
        return out

    def _on_channel_limit_changed(self, _index: int) -> None:
        self._rebuild_cards()

    def _score_tooltip(self, label: str, row: dict, avg_views: float,
                       raw_score: float, gauge_value: float) -> str:
        """Header (label + final score) plus the actual numbers plugged into
        post_score_raw's formula for this specific post — so hovering a card
        answers "why this score" without needing to open the module
        docstring."""
        views = int(row.get("views", 0) or 0)
        reactions = int(row.get("reactions", 0) or 0)
        forwards = int(row.get("forwards", 0) or 0)
        comments = min(int(row.get("comments", 0) or 0), _COMMENT_CAP)
        reaction_weighted = _reaction_weighted(reactions)
        viral_excess = max(0.0, views - avg_views)
        weighted = (forwards * _FORWARD_WEIGHT + comments * _COMMENT_WEIGHT
                   + reaction_weighted + viral_excess * _VIRAL_WEIGHT)
        erv_pct = (weighted / views * 100) if views else 0.0
        header = self.tr_("cqi_post_tooltip", label=label, score=f"{raw_score:.1f}")
        formula = self.tr_(
            "cqi_post_tooltip_formula",
            fwd_w=f"{_FORWARD_WEIGHT:g}", cmt_w=f"{_COMMENT_WEIGHT:g}",
            t1cap=fmt_int(_REACTION_TIER1_CAP), t1w=f"{_REACTION_TIER1_WEIGHT:g}",
            t2cap=fmt_int(_REACTION_TIER2_CAP), t2w=f"{_REACTION_TIER2_WEIGHT:g}",
            vrl_w=f"{_VIRAL_WEIGHT:g}", forwards=fmt_int(forwards),
            comments=fmt_int(comments), reactions=fmt_int(reactions),
            reaction_weighted=f"{reaction_weighted:.2f}",
            avg_views=fmt_int(round(avg_views)),
            viral_excess=fmt_int(round(viral_excess)),
            views=fmt_int(views), erv=f"{erv_pct:.2f}", raw=f"{raw_score:.1f}",
            k=f"{_POST_GAUGE_K:g}", gauge=round(gauge_value))
        return f"{header}\n\n{formula}"

    def _rebuild_cards(self) -> None:
        # See ContentQualityView's earlier channel-card version of this
        # bug: takeAt() unmanages a widget from the layout but doesn't hide
        # it, and deleteLater() doesn't destroy it until the next event
        # loop pass — hide immediately so a rapid double-rebuild can't
        # briefly show stale cards on top of fresh ones.
        for i in reversed(range(self.grid.count())):
            item = self.grid.takeAt(i)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        self._cards = []

        has_channels = bool(self._channels)
        has_posts = bool(self._post_entries)
        self.empty_posts_lbl.setVisible(has_channels and not has_posts)
        self.grid_holder.setVisible(has_posts)

        for entry in self._channel_limited_entries():
            ch = entry["channel"]
            row = entry["row"]
            card = _PostCard()

            label = _channel_label(ch)
            text = _elide_to_lines(row.get("text") or "", _TEXT_WIDTH, _TEXT_LINES,
                                   _TEXT_PIXEL_SIZE)
            channel_ref = _channel_ref(ch)
            link = build_post_link(channel_ref, row.get("id", 0))

            thumb_path = thumbnail_path(channel_ref, row.get("id", 0))
            thumb = None
            if thumb_path.exists():
                pix = QPixmap(str(thumb_path))
                if not pix.isNull():
                    thumb = pix.scaled(
                        CARD_WIDTH - 20, _THUMB_HEIGHT, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)
            placeholder = _MEDIA_PLACEHOLDERS.get(row.get("media_type") or "", "")

            views = int(row.get("views", 0) or 0)
            reactions = int(row.get("reactions", 0) or 0)
            forwards = int(row.get("forwards", 0) or 0)
            comments = int(row.get("comments", 0) or 0)
            counts_text = (f"{fmt_int(views)}👁️ {fmt_int(comments)}💬\n"
                          f"{fmt_int(reactions)}❤️ {fmt_int(forwards)}🔄")
            gauge_value = post_gauge_value(entry["raw_score"])
            tooltip = self._score_tooltip(label, row, _channel_avg_views(ch),
                                          entry["raw_score"], gauge_value)

            card.set_data(label, thumb, placeholder, text, gauge_value, counts_text,
                         link, tooltip)
            self._cards.append(card)

        self._cols = 0  # force _relayout_grid to actually place the new cards
        self._relayout_grid()

    def _relayout_grid(self) -> None:
        if not self._cards:
            return
        avail = max(self.page_scroll.viewport().width()
                    - _PAGE_MARGIN_LEFT - _PAGE_MARGIN_RIGHT, CARD_WIDTH)
        cols = max(1, (avail + _GRID_SPACING) // (CARD_WIDTH + _GRID_SPACING))
        if cols == self._cols:
            return
        self._cols = cols
        for i in reversed(range(self.grid.count())):
            self.grid.takeAt(i)
        for i, card in enumerate(self._cards):
            self.grid.addWidget(card, i // cols, i % cols)
        rows = -(-len(self._cards) // cols)  # ceil division
        for r in range(256):
            self.grid.setRowStretch(r, 0)
        for c in range(256):
            self.grid.setColumnStretch(c, 0)
        self.grid.setRowStretch(rows, 1)
        self.grid.setColumnStretch(cols, 1)

    # ------------------------------------------------------- fetch media
    def _on_fetch_media_clicked(self) -> None:
        entries = self._channel_limited_entries()
        if not entries or self._media_worker is not None:
            return
        conn = {
            "api_id": self.cfg.get("API_ID").strip(),
            "api_hash": self.cfg.get("API_HASH").strip(),
            "phone": self.cfg.get("PHONE_NUMBER").strip(),
            "session": self.cfg.session_path(),
        }
        if not conn["api_id"] or not conn["api_hash"]:
            QMessageBox.information(self, self.tr_("app_title"),
                                    self.tr_("cqi_fetch_media_need_login"))
            return

        posts = [{"channel": _channel_ref(e["channel"]), "id": e["row"].get("id", 0),
                 "ids": e["row"].get("ids") or [e["row"].get("id", 0)]}
                 for e in entries]
        self.fetch_media_btn.setEnabled(False)
        self.fetch_media_btn.setText(self.tr_("cqi_fetch_media_running"))
        self._set_media_log("")
        self._media_worker = ToolWorker(run_thumbnail_cache, {"posts": posts}, conn, parent=self)
        self._media_worker.sig_log.connect(self._set_media_log)
        self._media_worker.sig_ask.connect(self._on_media_ask)
        self._media_worker.sig_done.connect(self._on_fetch_media_done)
        self._media_worker.start()

    def _set_media_log(self, msg: str) -> None:
        # Measured with a fresh QFont at the QSS pixel size, not the
        # label's own .fontMetrics() — an unpolished widget's QFont doesn't
        # yet reflect the global QSS font-size rule (see _elide_to_lines).
        msg = msg.strip()
        font = QFont()
        font.setPixelSize(_MEDIA_LOG_PIXEL_SIZE)
        elided = QFontMetrics(font).elidedText(
            msg, Qt.TextElideMode.ElideRight, _MEDIA_LOG_WIDTH)
        self.media_log_lbl.setText(elided)
        self.media_log_lbl.setToolTip(msg)

    def _on_media_ask(self, _kind: str, _prompt: str) -> None:
        # This button assumes the session already used for regular channel
        # fetches is still authorized — if Telegram unexpectedly needs a
        # fresh login here, send the user to Config rather than building a
        # second inline login flow just for this.
        QMessageBox.information(self, self.tr_("app_title"),
                                self.tr_("cqi_fetch_media_login_required"))
        if self._media_worker is not None:
            self._media_worker.request_cancel()

    def _on_fetch_media_done(self, ok: bool, msg: str) -> None:
        self.fetch_media_btn.setEnabled(True)
        self.fetch_media_btn.setText(self.tr_("cqi_fetch_media"))
        self._media_worker = None
        if ok:
            self._rebuild_cards()  # pick up newly cached thumbnails
        elif msg and msg != "Login cancelled":
            QMessageBox.warning(self, self.tr_("app_title"), msg)

    # ------------------------------------------------------------ tg links
    def _current_period_label(self) -> str:
        if self._period_mode == "all":
            return self.tr_("period_mode_all")
        btn = self._period_btn_group.checkedButton()
        return btn.text() if btn else ""

    def _build_tg_links_text(self) -> str:
        header = self.tr_("cqi_tg_links_header", folder=self.folder_combo.currentText(),
                          period=self._current_period_label())
        lines = [header]
        for i, entry in enumerate(self._channel_limited_entries(), 1):
            row = entry["row"]
            score = round(post_gauge_value(entry["raw_score"]))
            text = " ".join((row.get("text") or "").split())
            snippet = text[:24] + ("…" if len(text) > 24 else "")
            link = build_post_link(_channel_ref(entry["channel"]), row.get("id", 0))
            link = link.removeprefix("https://")
            lines.append(f"{i}. {score} 📊 {snippet} {link}")
        return "\n".join(lines)

    def _on_tg_links_clicked(self) -> None:
        if not self._post_entries:
            QMessageBox.information(self, self.tr_("app_title"), self.tr_("cqi_empty_posts"))
            return
        ChannelReportDialog(self, self.i18n, self._build_tg_links_text(),
                            title=self.tr_("cqi_tg_links_dialog_title")).exec()

    # ---------------------------------------------------------- translate
    def retranslate(self) -> None:
        self.title_lbl.setText(self.tr_("nav_content_quality"))
        self.fetch_media_btn.setToolTip(self.tr_("cqi_fetch_media_hint"))
        if self._media_worker is None:
            self.fetch_media_btn.setText(self.tr_("cqi_fetch_media"))
        self.tg_links_btn.setText(self.tr_("cqi_tg_links"))
        self.tg_links_btn.setToolTip(self.tr_("cqi_tg_links_hint"))
        self.tg_links_limit_combo.setToolTip(self.tr_("cqi_tg_links_limit_hint"))
        self.tg_links_limit_combo.setItemText(0, self.tr_("cqi_tg_links_limit_none"))
        for i in range(1, self.tg_links_limit_combo.count()):
            n = self.tg_links_limit_combo.itemData(i)
            self.tg_links_limit_combo.setItemText(i, self.tr_("cqi_tg_links_limit_n", n=n))
        self.pick_lbl.setText(self.tr_("folder_stat_pick_folder"))
        self.no_folders_lbl.setText(self.tr_("folder_stat_no_folders"))
        self.empty_channels_lbl.setText(self.tr_("folder_stat_empty_channels"))
        self.empty_posts_lbl.setText(self.tr_("cqi_empty_posts"))
        self.mode_season_btn.setText(self.tr_("period_mode_season"))
        self.mode_month_btn.setText(self.tr_("period_mode_month"))
        self.mode_all_btn.setText(self.tr_("period_mode_all"))
        # Card tooltips embed translated text (see _score_tooltip) — rebuild
        # so they don't stay stuck in whatever language was active when the
        # cards were last built.
        self._rebuild_cards()
