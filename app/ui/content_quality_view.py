"""Content Quality Index view: for one folder, a grid of gauge cards scoring
each channel on content quality rather than reach — how well posts perform
relative to the channel's own audience, not how big the audience is, how
often it posts, or how many members it has.

Input per post: views, reactions, reposts. Channel-level aggregates come
from `stats` (already accurate over every scanned post, from a single pass
over the channel's full history — see folder_stat_view's module docstring):
avg_views, avg_reactions, and a robust avg_reposts_trimmed.

--------------------------------------------------------------- robustness
Two pre-processing steps, both driven by real single-post outliers found
in this app's own checkpoints — a repost count in particular is far more
top-heavy than views or reactions (one giveaway or forward chain can be a
huge share of a channel's *entire* repost total across a thousand posts):

* avg_reposts_trimmed — sort every post by reposts ascending, drop the top
  10%, mean the rest (see `_trimmed_mean_drop_top` in channel_stat.py,
  computed once during the fetch scan and stored in the checkpoint). This
  removes the whole fat tail of occasional reshare spikes, not just the
  single largest one. Checkpoints fetched before this field existed fall
  back to a cruder approximation (exclude just the single most-reposted
  post) until refetched — see `_trimmed_avg_reposts`.

* Virality Index = min(max_views / avg_views, 10) — a single post that got
  reposted elsewhere and picked up a flood of external views (not organic
  channel engagement) can otherwise send this ratio to 30-40× and dominate
  the whole score. Real data has a clean natural break here: p90 across
  this app's checkpoints is ~6.5×, then a handful of outliers jump straight
  to 14-38× — so 10× is capped as "very viral" without over-crediting a
  single freak post.

--------------------------------------------------------------------- CQI
    ERV% = (avg_reactions + avg_reposts_trimmed) / avg_views × 100
    CQI  = ERV% × Virality Index × 100

CQI is a positive, unbounded number (typically 200-700 in practice; a
channel with a very strong outlier post can still score several thousand
even after capping) — see `cqi_gauge_value` for how it's mapped onto the
0-1000 gauge. Both ERV% and Virality Index are per-post ratios, so neither
depends on how many posts the channel made or how many members it has.

An earlier version computed ERV%/Virality Index per calendar month and
took the median across months instead of over the full history — but a
month's max/avg ratio is an order statistic that mechanically grows with
how many posts happened that month (a single-post month is *always*
exactly 1.0×), which biased the whole score toward however often a
channel happens to post rather than how good its content is. Verified
empirically: across this app's real checkpoints, median monthly virality
index climbed from 1.00× at 1 post/month to 1.31× at 15+ posts/month with
no change in actual content quality — pure sample-size artifact.

---------------------------------------------------------- 3 submetrics
Each card also breaks that down into three at-a-glance "strong side"
submetrics, always 0-100. All three go through the same saturating curve,
score = raw / (raw + K) × 100, so no metric hits a hard ceiling and the
whole 0-100 range stays usable — a hard clamp is worse here: real repost
rates live in 0-1%, so directly displaying (or clamping) that raw
percentage rounds almost every channel to "0", and real Viral Boost often
exceeds 100% raw, so a hard clamp there pinned the *majority* of channels
at 100 instead of just extreme ones.

    ❤️ Reaction Depth = saturate(avg_reactions / avg_views,         K=0.015)
    🔄 Shareability   = saturate(avg_reposts_trimmed / avg_views,   K=0.005)
    🚀 Viral Boost    = saturate((Virality Index − 1) × 100,        K=100)

Reaction Depth and Shareability's raw values are plain fractions here
(0.027 for a 2.7% reaction rate, not 2.7) — K is in the same units. K is
chosen per-metric so a typical "good" channel lands in the 50-70 range
(e.g. a 2.7% reaction rate → ≈64, a 0.6% repost rate → ≈55, a +145% viral
boost → ≈59); tune it if this app's real distribution shifts.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout,
    QWidget,
)

from ..folders import FolderStore
from ..store import ChannelStore
from .charts import GaugeDial
from .widgets import Card, hline

CARD_WIDTH = 130
CARD_HEIGHT = 150
GAUGE_MAX = 1000
_GRID_SPACING = 14


def _channel_label(ch: dict) -> str:
    username = ch.get("username") or ""
    if username:
        return f"@{username}"
    return ch.get("title") or ch.get("channel") or ch.get("key", "?")


def _trimmed_avg_reposts(stats: dict) -> float:
    """avg_reposts_trimmed — a true top-10%-dropped mean computed during
    the fetch scan (see channel_stat.py's _trimmed_mean_drop_top) — see
    the module docstring for why. Checkpoints fetched before that field
    existed fall back to a cruder approximation (exclude just the single
    most-reposted post) until refetched."""
    trimmed = stats.get("avg_reposts_trimmed")
    if trimmed is not None:
        return trimmed
    avg_reposts = stats.get("avg_reposts", 0) or 0
    total_posts = stats.get("total_posts", 0) or 0
    max_reposts = stats.get("max_reposts", 0) or 0
    if total_posts <= 1:
        return avg_reposts
    total_reposts = avg_reposts * total_posts
    return max(0.0, (total_reposts - max_reposts) / (total_posts - 1))


# See the module docstring — real virality index across this app's
# checkpoints has a clean natural break around p90 (~6.5×) before a
# handful of single-post outliers jump to 14-38×.
_VIRALITY_INDEX_CAP = 10.0


def _capped_virality_index(stats: dict) -> float:
    """max_views / avg_views, capped at _VIRALITY_INDEX_CAP so one freak
    post (often boosted by external reposts, not organic engagement)
    can't dominate CQI or Viral Boost on its own."""
    avg_views = stats.get("avg_views", 0) or 0
    if not avg_views:
        return 0.0
    max_views = stats.get("max_views", 0) or 0
    return min(max_views / avg_views, _VIRALITY_INDEX_CAP)


def _saturate(raw: float, k: float) -> float:
    """Map an unbounded-above non-negative value onto [0, 100) via
    raw/(raw+k) rather than a hard clamp — see the module docstring's "3
    submetrics" section for why a hard clamp is worse here. `raw` and `k`
    just need to be in the same units (fraction vs. percent) — the curve
    shape doesn't care which."""
    if raw <= 0:
        return 0.0
    return 100.0 * raw / (raw + k)


# K per submetric — see the module docstring's "3 submetrics" section for
# how these were picked (typical "good" channel lands around 50-70).
# Reaction Depth / Shareability's raw values are fractions (0.027, not
# 2.7), so their K is too; Viral Boost's raw is a percent-like number
# ((VI-1)×100), so its K is on that scale instead.
_REACTION_DEPTH_K = 0.015
_SHAREABILITY_K = 0.005
_VIRAL_BOOST_SATURATION_K = 100.0


def channel_submetrics(data: dict) -> tuple[float, float, float]:
    """(Reaction Depth, Shareability, Viral Boost), each 0-100 — see module
    docstring for the formulas. All 0 if there isn't enough data yet."""
    stats = data.get("stats", {})
    avg_views = stats.get("avg_views", 0) or 0
    if not avg_views:
        return 0.0, 0.0, 0.0
    avg_reactions = stats.get("avg_reactions", 0) or 0
    avg_reposts = _trimmed_avg_reposts(stats)
    reaction_rate = avg_reactions / avg_views       # fraction, e.g. 0.027
    repost_rate = avg_reposts / avg_views           # fraction, e.g. 0.006
    viral_boost_raw = (_capped_virality_index(stats) - 1) * 100

    reaction_depth = _saturate(reaction_rate, _REACTION_DEPTH_K)
    shareability = _saturate(repost_rate, _SHAREABILITY_K)
    viral_boost = _saturate(viral_boost_raw, _VIRAL_BOOST_SATURATION_K)
    return reaction_depth, shareability, viral_boost


def channel_cqi(data: dict) -> float:
    """ERV% × Virality Index × 100 for one channel checkpoint, from its
    full-history stats — see the module docstring for why. Unbounded above
    (a channel with a very strong — even after capping — outlier post can
    still score several thousand) — see `cqi_gauge_value` for how this
    gets mapped onto the 0-1000 gauge. 0 if the channel hasn't been
    fetched (or refetched) since `stats.avg_views` existed."""
    stats = data.get("stats", {})
    avg_views = stats.get("avg_views", 0) or 0
    if not avg_views:
        return 0.0
    avg_reactions = stats.get("avg_reactions", 0) or 0
    avg_reposts = _trimmed_avg_reposts(stats)
    erv_pct = (avg_reactions + avg_reposts) / avg_views * 100
    virality_index = _capped_virality_index(stats)
    return erv_pct * virality_index * 100


# Real median raw CQI across this app's checkpoints (post-capping, ~700) —
# chosen so a typical channel lands near the middle of the gauge.
_GAUGE_SATURATION_K = 700.0


def cqi_gauge_value(raw_cqi: float) -> float:
    """Map an unbounded raw CQI onto 0-1000 for the gauge — see `_saturate`."""
    return GAUGE_MAX / 100.0 * _saturate(raw_cqi, _GAUGE_SATURATION_K)


class _GaugeCard(Card):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 10, 8, 8)
        lay.setSpacing(4)
        # Single line, hard-cut (no ellipsis) rather than wrapped — a
        # wrapped title on a small card ate too much vertical space and
        # pushed cards apart; wordWrap(False) just clips at the card edge.
        self.name_lbl = QLabel("—")
        self.name_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_lbl.setWordWrap(False)
        self.name_lbl.setObjectName("hint")
        lay.addWidget(self.name_lbl)

        self.gauge = GaugeDial(0, GAUGE_MAX)
        self.gauge.setFixedSize(76, 76)
        lay.addWidget(self.gauge, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.metrics_lbl = QLabel("")
        self.metrics_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.metrics_lbl.setWordWrap(False)
        # A plain setFont() here gets clobbered later: theme.py's global
        # `QWidget { font-size: 14px; }` rule outranks a programmatic font
        # once this widget is polished/shown (confirmed — setFont() had zero
        # visible effect, the label still rendered at the QSS's 14px). An
        # instance-level stylesheet beats an ancestor-level selector, so set
        # font-size here directly.
        self.metrics_lbl.setStyleSheet("font-size: 9px;")
        lay.addWidget(self.metrics_lbl)

    def set_data(self, label: str, gauge_value: float, metrics_text: str,
                tooltip: str) -> None:
        self.gauge.setValue(max(0, min(GAUGE_MAX, round(gauge_value))))
        self.metrics_lbl.setText(metrics_text)
        self.name_lbl.setText(label)
        self.setToolTip(tooltip)


class ContentQualityView(QWidget):
    def __init__(self, i18n, folder_store: FolderStore, channel_store: ChannelStore,
                 parent=None) -> None:
        super().__init__(parent)
        self.i18n = i18n
        self.folder_store = folder_store
        self.channel_store = channel_store
        self._cards: list[_GaugeCard] = []
        self._cols = 0
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
        self.title_lbl = QLabel(self.tr_("nav_content_quality"))
        self.title_lbl.setObjectName("pageTitle")
        header.addWidget(self.title_lbl)
        self.sub_lbl = QLabel(self.tr_("cqi_sub"))
        self.sub_lbl.setObjectName("pageSub")
        # This is the literal formula (see i18n "cqi_sub"), not prose — make
        # it selectable so it can be copied out verbatim.
        self.sub_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.sub_lbl.setCursor(Qt.CursorShape.IBeamCursor)
        header.addWidget(self.sub_lbl)
        outer.addLayout(header)

        pick_row = QHBoxLayout()
        self.pick_lbl = QLabel(self.tr_("folder_stat_pick_folder"))
        pick_row.addWidget(self.pick_lbl)
        self.folder_combo = QComboBox()
        self.folder_combo.currentIndexChanged.connect(self._on_folder_changed)
        pick_row.addWidget(self.folder_combo, 1)
        outer.addLayout(pick_row)
        outer.addWidget(hline())

        self.no_folders_lbl = QLabel(self.tr_("folder_stat_no_folders"))
        self.no_folders_lbl.setObjectName("navEmpty")
        self.no_folders_lbl.setWordWrap(True)
        outer.addWidget(self.no_folders_lbl)

        self.empty_channels_lbl = QLabel(self.tr_("folder_stat_empty_channels"))
        self.empty_channels_lbl.setObjectName("navEmpty")
        self.empty_channels_lbl.setWordWrap(True)
        outer.addWidget(self.empty_channels_lbl)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid_holder = QWidget()
        self.grid = QGridLayout(self.grid_holder)
        self.grid.setSpacing(_GRID_SPACING)
        self.grid.setContentsMargins(0, 6, 8, 6)
        # Otherwise a folder with only a few channels — fewer rows than fit
        # the viewport — has QGridLayout spread the leftover vertical space
        # evenly across every row instead of leaving it below the cards.
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll.setWidget(self.grid_holder)
        outer.addWidget(self.scroll, 1)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
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
        channels: list[dict] = []
        if folder_id:
            keys = [k for k, fid in self.folder_store.assignments.items() if fid == folder_id]
            for key in keys:
                data = self.channel_store.load(key)
                if data:
                    data.setdefault("key", key)
                    channels.append(data)

        has_folders = self.folder_combo.count() > 0
        has_channels = bool(channels)
        self.no_folders_lbl.setVisible(not has_folders)
        self.pick_lbl.setVisible(has_folders)
        self.folder_combo.setVisible(has_folders)
        self.empty_channels_lbl.setVisible(has_folders and not has_channels)
        self.scroll.setVisible(has_channels)

        self._rebuild_cards(channels)

    def _rebuild_cards(self, channels: list[dict]) -> None:
        # `refresh()` can call this indirectly twice in a row (setCurrentIndex
        # firing currentIndexChanged, then its own explicit reload) — takeAt()
        # only unmanages a widget from the layout, it doesn't hide it, and
        # deleteLater() doesn't actually destroy it until the next event-loop
        # pass, so without an explicit hide() a second rebuild could briefly
        # show stale cards still sitting on top of the fresh ones.
        for i in reversed(range(self.grid.count())):
            item = self.grid.takeAt(i)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        self._cards = []

        scored = sorted(((ch, channel_cqi(ch)) for ch in channels),
                        key=lambda t: t[1], reverse=True)
        for ch, raw_score in scored:
            card = _GaugeCard()
            label = _channel_label(ch)
            reaction_depth, shareability, viral_boost = channel_submetrics(ch)
            metrics_text = f"{reaction_depth:.0f}%❤️ {shareability:.0f}%🔄 {viral_boost:.0f}%🚀"
            gauge_value = cqi_gauge_value(raw_score)
            tooltip = self._cqi_tooltip(label, ch, raw_score, gauge_value)
            card.set_data(label, gauge_value, metrics_text, tooltip)
            self._cards.append(card)

        self._cols = 0  # force _relayout_grid to actually place the new cards
        self._relayout_grid()

    def _cqi_tooltip(self, label: str, data: dict, raw_cqi: float, gauge_value: float) -> str:
        stats = data.get("stats", {})
        avg_views = stats.get("avg_views", 0) or 0
        avg_reactions = stats.get("avg_reactions", 0) or 0
        avg_reposts = _trimmed_avg_reposts(stats)  # matches what channel_cqi actually used
        max_views = stats.get("max_views", 0) or 0
        erv_pct = (avg_reactions + avg_reposts) / avg_views * 100 if avg_views else 0
        virality_raw = max_views / avg_views if avg_views else 0
        virality_index = _capped_virality_index(stats)  # matches what channel_cqi actually used
        text = self.tr_(
            "cqi_score_tooltip", label=label, cqi=f"{raw_cqi:.1f}",
            reactions=f"{avg_reactions:.1f}", reposts=f"{avg_reposts:.1f}",
            views=f"{avg_views:.1f}", erv=f"{erv_pct:.1f}",
            max_views=f"{max_views:.0f}", virality=f"{virality_index:.2f}",
            gauge=f"{gauge_value:.0f}")
        if virality_raw > _VIRALITY_INDEX_CAP:
            text += "\n" + self.tr_("cqi_tooltip_capped_note", raw=f"{virality_raw:.2f}",
                                    cap=f"{_VIRALITY_INDEX_CAP:.0f}")
        return text

    def _relayout_grid(self) -> None:
        if not self._cards:
            return
        avail = max(self.scroll.viewport().width(), CARD_WIDTH)
        cols = max(1, (avail + _GRID_SPACING) // (CARD_WIDTH + _GRID_SPACING))
        if cols == self._cols:
            return
        self._cols = cols
        for i in reversed(range(self.grid.count())):
            self.grid.takeAt(i)
        for i, card in enumerate(self._cards):
            self.grid.addWidget(card, i // cols, i % cols)
        rows = -(-len(self._cards) // cols)  # ceil division
        # Reset any stale stretch factors from a previous layout (different
        # card/column count), then pin a single trailing phantom row/column
        # to absorb leftover viewport space instead of it spreading evenly
        # across the real rows (see the AlignTop|AlignLeft comment above).
        for r in range(256):
            self.grid.setRowStretch(r, 0)
        for c in range(256):
            self.grid.setColumnStretch(c, 0)
        self.grid.setRowStretch(rows, 1)
        self.grid.setColumnStretch(cols, 1)

    # ---------------------------------------------------------- translate
    def retranslate(self) -> None:
        self.title_lbl.setText(self.tr_("nav_content_quality"))
        self.sub_lbl.setText(self.tr_("cqi_sub"))
        self.pick_lbl.setText(self.tr_("folder_stat_pick_folder"))
        self.no_folders_lbl.setText(self.tr_("folder_stat_no_folders"))
        self.empty_channels_lbl.setText(self.tr_("folder_stat_empty_channels"))
