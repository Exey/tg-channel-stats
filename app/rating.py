"""Composite per-channel-per-period Rating — shared by the Folder Stats view
(app.ui.folder_stat_view, which computes it per period bucket within one
folder) and the Config screen's Folders MD export (app.ui.config_view, which
reuses it so a channel's exported Rating matches what Folder Stats would show
for the same folder/period). Kept here, not in either view, so neither has to
import UI code from the other to reuse it.

score_entries() takes one period bucket's entries — one dict per channel,
with `views`, `shares`, `reactions`, `quality` (see app.scoring) and
`viral_share` already filled in — and adds a `score` key to each, in [0, 1]:

    views      = min-max normalized against the other entries in the same
                 bucket, weighted VIEWS_WEIGHT
    engagement = shares + REACTIONS_ENGAGEMENT_WEIGHT × reactions, min-max
                 normalized the same way, weighted ENGAGEMENT_WEIGHT —
                 reactions count for only 5% of their raw value here (vs.
                 shares counting in full) because reaction counts are the
                 easier of the two to artificially pump; a repost is a
                 more costly, harder to fake action
    quality    = per-post gauge score (app.scoring), averaged per channel
                 per period, min-max normalized, weighted QUALITY_WEIGHT
    virality   = an absolute (not normalized) curve over viral_share — see
                 virality_component() — weighted by virality_weight(viral_
                 share), which itself ramps from VIRALITY_WEIGHT_MIN to
                 VIRALITY_WEIGHT_MAX as viral_share climbs to 15%, so a
                 highly viral channel isn't just scored well on virality,
                 virality also matters more to its rating

The four *_WEIGHT constants are relative, not a budget that has to sum to
1 — the composite is their weighted average, always divided by their actual
total (VIEWS_WEIGHT + ENGAGEMENT_WEIGHT + QUALITY_WEIGHT + virality_weight)
for that entry. That division is the one and only normalization step, not a
conditional safety net for an edge case — so each constant's *relative* size
is a direct, honest read of how much it swings the score at every viral_share
level, with no hidden threshold past which some other weight silently stops
being honored. (An earlier version instead gave views only whatever budget
happened to be left over after fixing the other three to sum near 1 — which
went negative, and floored at 0, for most realistic viral_share values once
those three were tuned up; a floor-at-0 "safety net" that fires on almost
every entry isn't one. Giving views its own explicit weight instead removes
that failure mode entirely, not just patches its symptom.)

A channel with ~0 reposts in the period is a red flag views/reactions/
quality alone don't catch — it suggests whatever views or apparent virality
the channel shows weren't corroborated by anyone actually sharing the
content. So its viral_share is first cut by ZERO_REPOSTS_PENALTY *before*
the virality curve above (denying it credit for an apparently-viral post),
and the same penalty is then applied again as a flat cut to the whole
composite score.
"""
from __future__ import annotations

VIEWS_WEIGHT = 0.7             # the single biggest weight — views matter most
ENGAGEMENT_WEIGHT = 0.65        # shares/reposts, second only to views
QUALITY_WEIGHT = 0.45           # third Quality which decribed in scoring.py
VIRALITY_WEIGHT_MIN = 0.10
VIRALITY_WEIGHT_MAX = 0.51
REACTIONS_ENGAGEMENT_WEIGHT = 0.05  # reactions are easy to pump; shares count in full
ZERO_REPOSTS_PENALTY = 0.4  # rating cut for a channel with ~0 reposts — see module docstring


def virality_component(viral_share: float) -> float:
    """0-1 virality score from an *absolute* viral-share percentage (not
    normalized against other channels): 0% -> 0, 1% -> 0.05, ramping up to
    1.0 at 15%+ viral share."""
    if viral_share <= 0:
        return 0.0
    if viral_share >= 15:
        return 1.0
    if viral_share <= 1:
        return 0.05 * viral_share
    return 0.05 + (viral_share - 1) * (1.0 - 0.05) / (15 - 1)


def virality_weight(viral_share: float) -> float:
    """How much of the rating virality can swing, itself scaled by how
    viral the channel is: VIRALITY_WEIGHT_MIN at 0% viral share, ramping
    linearly to VIRALITY_WEIGHT_MAX at 15%+ viral share."""
    lo, hi = VIRALITY_WEIGHT_MIN, VIRALITY_WEIGHT_MAX
    if viral_share <= 0:
        return lo
    if viral_share >= 15:
        return hi
    return lo + (viral_share / 15) * (hi - lo)


def score_entries(entries: list[dict]) -> None:
    """Fill in a `score` key (0-1) on each entry in place — see module
    docstring for the formula."""
    if not entries:
        return
    views_vals = [e["views"] for e in entries]
    eng_vals = [e["shares"] + REACTIONS_ENGAGEMENT_WEIGHT * e["reactions"] for e in entries]
    quality_vals = [e["quality"] for e in entries]
    vw_min, vw_max = min(views_vals), max(views_vals)
    eg_min, eg_max = min(eng_vals), max(eng_vals)
    qa_min, qa_max = min(quality_vals), max(quality_vals)
    for e in entries:
        views_norm = (e["views"] - vw_min) / (vw_max - vw_min) if vw_max != vw_min else 0
        eng = e["shares"] + REACTIONS_ENGAGEMENT_WEIGHT * e["reactions"]
        eng_norm = (eng - eg_min) / (eg_max - eg_min) if eg_max != eg_min else 0
        quality_norm = ((e["quality"] - qa_min) / (qa_max - qa_min)
                        if qa_max != qa_min else 0)
        zero_reposts = e["shares"] == 0
        viral_share_for_score = e["viral_share"]
        if zero_reposts:
            viral_share_for_score *= 1.0 - ZERO_REPOSTS_PENALTY
        v_score = virality_component(viral_share_for_score)
        v_weight = virality_weight(viral_share_for_score)
        total_weight = VIEWS_WEIGHT + ENGAGEMENT_WEIGHT + QUALITY_WEIGHT + v_weight
        e["score"] = (VIEWS_WEIGHT * views_norm
                     + ENGAGEMENT_WEIGHT * eng_norm
                     + QUALITY_WEIGHT * quality_norm
                     + v_weight * v_score) / total_weight
        if zero_reposts:
            e["score"] *= 1.0 - ZERO_REPOSTS_PENALTY
