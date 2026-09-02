"""Per-post content-quality scoring — shared by the High-Quality Posts view
(app.ui.content_quality_view, which ranks posts across many channels) and
the per-channel Dashboard (app.ui.dashboard_view, which uses the same
formula for its Quality trend line and its "recent posts" cards). Kept
here, not in either view, so neither has to import UI code from the other
to reuse it.

    comments      = min(comments, 100)   # capped — see below
    reaction_wt   = min(reactions, 1000) × 0.045
                  + max(0, min(reactions, 10000) − 1000) × 0.005
    viral_excess  = max(0, views − avg_views), then capped at
                    VIRAL_EXCESS_CAP_MULT × avg_views  # see below
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
beat its own channel's average views also earns a "viral excess" bonus
(floored at 0, so an under-average post gets neither bonus nor penalty),
weighted at 0.2. Left uncapped this term misbehaves: `views − avg_views`
grows without limit, and because the stored `rows` pool is *selected* for
high views, on a channel with a low average almost every pool post beats it
several-fold, pinning the whole pool near the term's ~20%-of-ERV asymptote
and drowning out forwards/reactions entirely (one channel's whole grid
reading ~700). So `viral_excess` is capped at VIRAL_EXCESS_CAP_MULT ×
avg_views: a post is credited for beating its average by up to ~2× and no
more — past that it has gone *viral*, which is a channel-level signal
(`viral_post_share`, and the Rating's own virality term), not a mark of the
post's craft. With the cap the term peaks (~10% of ERV) around 2× average
and then fades as raw views climb, so forwards and reactions decide the
ranking among a channel's genuine hits. Views themselves stay the ratio's
denominator throughout, never a directly-weighted term. An earlier version
of the High-Quality Posts view scored whole *channels* on a "Virality Index"
(max_views / avg_views, trimmed to tame single-post outliers), dropped once
that view switched to scoring individual posts — this capped viral-excess
term is a deliberate, narrower reintroduction of the same avg_views
comparison at the per-post level.

A post with an inline keyboard (`row["has_buttons"]`, set by
app.tools.channel_stat from Telethon's `Message.reply_markup`) scores 0
outright, before any of the above — ad/CTA posts commonly attach one, and
whatever engagement they draw is being pulled by the button, not the
content, so they shouldn't surface as "high quality" regardless of their
raw numbers. A post forwarded in from another channel (`row["repost"]`, see
app.tools.channel_stat._is_repost) likewise scores 0 — its views and
reactions were earned by the original author, not by the channel reposting
it, so it isn't a signal of *this* channel's content quality. Checkpoints
fetched before these fields existed just don't have them, so nothing is
excluded there until refetched.
"""
from __future__ import annotations

GAUGE_MAX = 1000

FORWARD_WEIGHT = 1.0
COMMENT_WEIGHT = 0.25
COMMENT_CAP = 100   # comments beyond this count the same as exactly 100

# Reactions are weighted in two brackets instead of one flat rate — see
# module docstring.
REACTION_TIER1_CAP = 1000
REACTION_TIER1_WEIGHT = 0.045
REACTION_TIER2_CAP = 10_000
REACTION_TIER2_WEIGHT = 0.005

# A post that pulled in more views than its own channel's average is
# rewarded for that too — see module docstring.
VIRAL_WEIGHT = 0.2
# ...but only for beating the average by up to this multiple; past that the
# post has gone viral (a channel-level signal), not shown better craft, and
# an uncapped `views − avg_views` otherwise pins a whole selected pool near
# the term's asymptote. See the module docstring.
VIRAL_EXCESS_CAP_MULT = 1.0

# Real median raw post score across this app's checkpoints (post the
# forward/comment/reaction/viral weighting above) — chosen so a typical
# post lands near the middle of the gauge.
POST_GAUGE_K = 580.0


def reaction_weighted(reactions: int) -> float:
    tier1 = min(reactions, REACTION_TIER1_CAP) * REACTION_TIER1_WEIGHT
    tier2 = max(0, min(reactions, REACTION_TIER2_CAP) - REACTION_TIER1_CAP) * REACTION_TIER2_WEIGHT
    return tier1 + tier2


def viral_excess_value(views: float, avg_views: float) -> float:
    """`max(0, views − avg_views)`, capped at VIRAL_EXCESS_CAP_MULT × avg_views
    so a genuine breakout is credited but a viral megahit isn't scored as
    ever-higher "quality" — see the module docstring. The cap is skipped when
    avg_views is unknown (0, a pre-`avg_views` checkpoint)."""
    excess = max(0.0, views - avg_views)
    if avg_views > 0:
        excess = min(excess, VIRAL_EXCESS_CAP_MULT * avg_views)
    return excess


def post_score_raw(row: dict, avg_views: float, *, viral_excess: bool = True) -> float:
    """ERV% × 100 for one post — see module docstring. `avg_views` is that
    post's own channel's average (checkpoint `stats.avg_views`).

    Scores 0 outright for a post with an inline keyboard (`has_buttons`) —
    an ad/CTA button is what's driving engagement there, not the content —
    or one forwarded in from another channel (`repost`, see
    app.tools.channel_stat._is_repost), whose engagement was earned by the
    original author rather than the reposting channel. Checkpoints fetched
    before those fields existed don't have them and so are never excluded
    until refetched.

    `viral_excess=False` drops the "beat your own channel's average views"
    bonus term (item 4 in the module docstring). app.rating passes this when
    it averages the gauge per channel for the composite Rating's *quality*
    term — that Rating already scores reach directly (its views term) and
    virality separately, so crediting a post's raw reach a third time inside
    "quality" just triple-counts the same signal. The standalone High-Quality
    Posts grid and the dashboard keep the term (the default)."""
    if row.get("has_buttons") or row.get("repost"):
        return 0.0
    views = int(row.get("views", 0) or 0)
    if not views:
        return 0.0
    reactions = int(row.get("reactions", 0) or 0)
    forwards = int(row.get("forwards", 0) or 0)
    comments = min(int(row.get("comments", 0) or 0), COMMENT_CAP)
    weighted = (forwards * FORWARD_WEIGHT + comments * COMMENT_WEIGHT
               + reaction_weighted(reactions))
    if viral_excess:
        weighted += viral_excess_value(views, avg_views) * VIRAL_WEIGHT
    erv_pct = weighted / views * 100
    return erv_pct * 100


def saturate(raw: float, k: float) -> float:
    """Map an unbounded-above non-negative value onto [0, 100) via
    raw/(raw+k) — see the module docstring for why a hard clamp is worse."""
    if raw <= 0:
        return 0.0
    return 100.0 * raw / (raw + k)


def post_gauge_value(raw_score: float) -> float:
    """Map an unbounded raw post score onto 0-1000 for the gauge."""
    return GAUGE_MAX / 100.0 * saturate(raw_score, POST_GAUGE_K)


def score_tooltip(tr, label: str, row: dict, avg_views: float,
                  raw_score: float, gauge_value: float, fmt_int) -> str:
    """Header (label + final score) plus the actual numbers plugged into
    post_score_raw's formula for this specific post — so hovering a card
    answers "why this score" without needing to open this module's
    docstring. `tr` is the caller's i18n.tr-style callable (needs the
    "cqi_post_tooltip"/"cqi_post_tooltip_formula" keys); `fmt_int` is
    dashboard_view.fmt_int (thousands-grouped int formatting), passed in
    rather than imported to avoid a UI->UI import here."""
    views = int(row.get("views", 0) or 0)
    reactions = int(row.get("reactions", 0) or 0)
    forwards = int(row.get("forwards", 0) or 0)
    comments = min(int(row.get("comments", 0) or 0), COMMENT_CAP)
    rw = reaction_weighted(reactions)
    viral_excess = viral_excess_value(views, avg_views)
    header = tr("cqi_post_tooltip", label=label, score=f"{raw_score:.1f}")
    erv_pct = (
        (forwards * FORWARD_WEIGHT + comments * COMMENT_WEIGHT + rw
         + viral_excess * VIRAL_WEIGHT) / views * 100
    ) if views else 0.0
    formula = tr(
        "cqi_post_tooltip_formula",
        fwd_w=f"{FORWARD_WEIGHT:g}", cmt_w=f"{COMMENT_WEIGHT:g}",
        t1cap=fmt_int(REACTION_TIER1_CAP), t1w=f"{REACTION_TIER1_WEIGHT:g}",
        t2cap=fmt_int(REACTION_TIER2_CAP), t2w=f"{REACTION_TIER2_WEIGHT:g}",
        vrl_w=f"{VIRAL_WEIGHT:g}", forwards=fmt_int(forwards),
        comments=fmt_int(comments), reactions=fmt_int(reactions),
        reaction_weighted=f"{rw:.2f}", avg_views=fmt_int(round(avg_views)),
        viral_excess=fmt_int(round(viral_excess)),
        views=fmt_int(views), erv=f"{erv_pct:.2f}", raw=f"{raw_score:.1f}",
        k=f"{POST_GAUGE_K:g}", gauge=round(gauge_value))
    return f"{header}\n\n{formula}"
