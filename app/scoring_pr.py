"""Mutual PR (ad-swap) scoring — shared by app.ui.mutual_pr_view, which lists
every tracked channel with an estimated ad-post follower-gain forecast and a
"best days to post" pick, to help decide which channels are worth trading ad
posts with.

The app has no real ad-campaign outcome data anywhere: ChannelStore keeps one
point-in-time snapshot per channel (no follower-history, no growth-over-time),
and every post record stores a single final view count with no view-age
curve (see app.tools.channel_stat). So unlike app.scoring's per-post formula
(tuned against this app's own real median), the forecast below is built on
constants that are genuinely just documented heuristics, not measurements —
they exist so a forecast can be produced at all, not because they were
calibrated against real ad-swap results:

    AD_VIEW_CURVE: assumed fraction of a post's eventual (settled) reach
        that has landed by each horizon. Shaped to match the accumulation
        curve implied by one real channel's own observed ad performance
        (~60% within 24h, ~75% by 48h, most of the rest trickling in over
        the following two weeks) — generalized into a fixed curve since no
        per-channel view-age data exists to calibrate it per channel.
    FOLLOW_CONVERSION_BASE: assumed fraction of a post's reach that converts
        into a new follower for an *ad* post specifically (as opposed to the
        channel's own organic content) — 2%, a commonly-cited but unverified
        Telegram ad rule of thumb, scaled by the channel's own content
        quality (see follow_conversion_rate) since a channel that engages
        its own audience well presumably also converts outside traffic
        better. The quality term's weight in that scaling was bumped 20%
        (1.0x -> 1.2x) on request, widening how much a channel's Interest
        can swing its conversion rate.

Both are ordinary module constants specifically so they're easy to retune
once real ad-swap outcomes are available to calibrate against.
"""
from __future__ import annotations

from datetime import datetime

from .periods import year_window_cutoff
from .scoring import post_gauge_value, post_score_raw

INTEREST_WINDOW_DAYS = 182   # "last ~6 months" — see channel_interest

# Fraction of a post's eventual (settled) reach assumed to have landed by
# each horizon — see module docstring. 24h trimmed to 82% of its original
# 0.60 (-> 0.49) on request, pulling the whole curve's most-visible number
# down a bit.
AD_VIEW_CURVE: dict[str, float] = {
    "24h": 0.49, "48h": 0.75, "72h": 0.78, "week": 0.80, "month": 1.00,
}

FOLLOW_CONVERSION_BASE = 0.02   # see module docstring
INTEREST_WEIGHT = 1.2   # 20% bump on request — see follow_conversion_rate

# Posts-in-between at which a repeat ad post's retention has decayed to
# half — see repeated_post_forecast. 16 is chosen so a channel posting at
# this app's real median avg_posts_per_day across its own tracked
# checkpoints (~0.525/day, i.e. ~15.75 posts across the 30-day gap) lands
# almost exactly at the ~50%-of-first-post figure asked for.
REPEAT_DECAY_POSTS_K = 16

# This app's real median avg_posts_per_day across its own tracked
# checkpoints — see rare_posting_penalty.
REFERENCE_POSTS_PER_DAY = 0.525

# Max extra credit a rare poster's lingering, un-buried visibility earns at
# each horizon beyond 24h — see rare_posting_boost. Week and month get
# noticeably more than 48h/72h since that advantage compounds the longer
# the post has had to keep collecting views nobody buried.
RARE_POSTING_BOOST_MAX: dict[str, float] = {
    "48h": 0.15, "72h": 0.25, "week": 0.40, "month": 0.60,
}

# How much of a "best day"'s raw deviation from an average day survives
# into its displayed rate — see best_days. 1.0 (no damping) produced
# swings like x2.1-x2.7 on real checkpoints, read as overconfident for
# what's ultimately a thin single-week-shape heuristic; 0.25 keeps the
# ranking (still sorted by the undamped score) but reports a gentler
# number.
BEST_DAY_RATE_DAMPING = 0.25


def _parse_date(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def channel_interest(rows: list[dict], avg_views: float,
                     days: int = INTEREST_WINDOW_DAYS) -> float:
    """Average post-quality gauge (app.scoring) over posts from the last
    `days`. Only as complete as the checkpoint's stored post pool for that
    window — see app.tools.channel_stat's top-N pool, the same limitation
    app.ui.folder_stat_view's windowed quality column already accepts.
    Falls back to every stored post if none fall in the window (e.g. an
    older checkpoint whose pool skews further back)."""
    cutoff = year_window_cutoff(days)
    windowed = [r for r in rows if (dt := _parse_date(r.get("date", ""))) and dt >= cutoff]
    pool = windowed or rows
    if not pool:
        return 0.0
    scores = [post_gauge_value(post_score_raw(r, avg_views)) for r in pool]
    return sum(scores) / len(scores)


def follow_conversion_rate(interest_gauge: float,
                           base: float = FOLLOW_CONVERSION_BASE) -> float:
    """Assumed reach-to-new-follower conversion rate for an ad post, scaled
    0.5x-1.7x the base rate by the channel's own content quality (0-1000
    gauge, weighted by INTEREST_WEIGHT) — see module docstring."""
    return base * (0.5 + INTEREST_WEIGHT * interest_gauge / 1000)


def rare_posting_penalty(avg_posts_per_day: float,
                         reference: float = REFERENCE_POSTS_PER_DAY) -> float:
    """0-1 factor cutting the 24h forecast for a channel that posts less
    often than REFERENCE_POSTS_PER_DAY (this app's real median). AD_VIEW_
    CURVE's 24h fraction assumes a post gets "buried" by the channel's own
    next post within a day or so, the normal case that pushes most of its
    eventual views into that first day. A rarely-posting channel's post
    instead stays at the top with nothing superseding it for days or
    weeks, so its audience keeps discovering it gradually over a much
    longer real timeline — the flat 24h fraction overstates that first day
    for such a channel. 1.0 (no penalty) at or above the reference
    frequency, scaling down proportionally below it."""
    if reference <= 0:
        return 1.0
    return min(1.0, avg_posts_per_day / reference)


def rare_posting_boost(horizon: str, avg_posts_per_day: float,
                       reference: float = REFERENCE_POSTS_PER_DAY) -> float:
    """>=1 factor boosting the 48h/72h/week/month forecast for a channel
    that posts less often than `reference` — the flip side of
    rare_posting_penalty's own reasoning: the same lack of a next post
    burying this one means it keeps collecting views from an audience that
    discovers it gradually, so by the time the longer horizons roll
    around a rare poster's post should be *ahead* of what the flat curve
    assumes for a typically-active channel, not just spared the 24h
    penalty. Scales linearly from 1.0 (no boost) at/above `reference` up
    to `1 + RARE_POSTING_BOOST_MAX[horizon]` as avg_posts_per_day -> 0."""
    rarity = 1 - rare_posting_penalty(avg_posts_per_day, reference)
    return 1 + RARE_POSTING_BOOST_MAX[horizon] * rarity


def ad_forecast(avg_views_settled: float, interest_gauge: float,
                avg_posts_per_day: float) -> dict[str, float]:
    """Estimated *new followers* gained by each horizon after an ad post —
    avg_views_settled × that horizon's AD_VIEW_CURVE fraction ×
    follow_conversion_rate, with the 24h figure further cut by
    rare_posting_penalty and every later horizon boosted by
    rare_posting_boost. See module docstring for why these are documented
    assumptions rather than measured curves."""
    rate = follow_conversion_rate(interest_gauge)
    forecast = {horizon: avg_views_settled * fraction * rate
               for horizon, fraction in AD_VIEW_CURVE.items()}
    forecast["24h"] *= rare_posting_penalty(avg_posts_per_day)
    for horizon in RARE_POSTING_BOOST_MAX:
        forecast[horizon] *= rare_posting_boost(horizon, avg_posts_per_day)
    return forecast


def repeated_post_forecast(forecast_24h: float, avg_posts_per_day: float,
                           days: int = 30) -> float:
    """Estimated new followers from a second/reminder ad post placed `days`
    (default 30 — "repeated after a month") after the first one, decayed by
    how many of the channel's *own* posts appeared in between and pushed
    the original down the feed: retention = 1 / (1 + posts_between / K),
    where posts_between = avg_posts_per_day × days and K =
    REPEAT_DECAY_POSTS_K. More intervening posts means more audience
    churn/forgetting, so a noisier channel's repeat performs worse than a
    quieter one's, both relative to that same channel's own 24h forecast."""
    posts_between = avg_posts_per_day * days
    retention = 1 / (1 + posts_between / REPEAT_DECAY_POSTS_K)
    return forecast_24h * retention


def best_days(weekday_counts: list[int], interest_gauge: float,
              top_n: int = 2) -> list[tuple[int, float]]:
    """(weekday index 0=Mon..6=Sun, rate) for the `top_n` days least crowded
    by the channel's own posting habits, i.e. best days to slot in an ad
    post without competing with the channel's own content —
    Best_Day_Score = (1 - normalized post rate) × quality, per weekday. If
    every weekday has the same count (including an all-zero, e.g.
    brand-new, channel), normalized post rate is 0 for all of them rather
    than dividing by zero, so the ranking falls back to interest alone (a
    tie, broken by weekday order). `rate` is that day's score relative to
    the week's average score, damped by BEST_DAY_RATE_DAMPING (e.g. a raw
    2.0x — double an average day — reports as 1.25x: 1 + (2.0-1)×0.25) —
    1.0 for every day when the average score is 0 (nothing to
    differentiate on). Ranking uses the undamped score; only the displayed
    rate is softened."""
    lo, hi = min(weekday_counts), max(weekday_counts)
    spread = hi - lo
    scores = []
    for day, count in enumerate(weekday_counts):
        normalized_rate = (count - lo) / spread if spread else 0.0
        scores.append((day, (1 - normalized_rate) * interest_gauge))
    avg_score = sum(s for _day, s in scores) / len(scores)
    rates = [(day, (score / avg_score) if avg_score else 1.0) for day, score in scores]
    rates.sort(key=lambda dr: dr[1], reverse=True)
    return [(day, 1 + (rate - 1) * BEST_DAY_RATE_DAMPING) for day, rate in rates[:top_n]]
