"""Fetch a channel: rank its posts by engagement *and* compute activity stats.

This is the extracted-and-extended `channel_top` tool. In a single pass over
the chosen period it does two jobs at once:

* engagement ranking (from tg-super-admin's channel_top) — per-post views,
  reactions and forwards ("private reposts"), album-merged, keeping the union
  of the top-N by each metric so the on-screen table can re-sort and still
  show the true leaders. Public reposts are fetched only for that pool.

* activity analytics (ported from telegram-channel-analyzer) — member count,
  creation date, posts/day, posts-with-media share, average/max views, and
  hour-of-day / day-of-week / month distributions for the charts.

The whole thing is returned as one JSON payload, which the app stores as a
per-channel checkpoint (see app.store).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from .common import resolve_entity

HEARTBEAT_EVERY = 500

# Period-of-analysis choices offered in the GUI, in calendar days. "all"
# (and any unrecognized/empty key) is intentionally absent — period_cutoff()
# treats a missing key as "no limit".
PERIOD_DAYS = {"3m": 90, "6m": 182, "1y": 365, "2y": 730, "3y": 1095}

# ERR% (engagement rate by reach) only looks at posts old enough that their
# view count has settled — freshly-posted messages are still accumulating
# views and would understate the rate.
ERR_MIN_AGE_DAYS = 14

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday"]


def period_cutoff(period: str) -> datetime | None:
    """UTC cutoff for a period key, or None for "all"/unrecognized/empty."""
    days = PERIOD_DAYS.get(period)
    return datetime.now(timezone.utc) - timedelta(days=days) if days else None


def _reaction_total(msg) -> int:
    reactions = getattr(msg, "reactions", None)
    if not reactions or not getattr(reactions, "results", None):
        return 0
    return sum(int(getattr(rc, "count", 0) or 0) for rc in reactions.results)


def _comment_total(msg) -> int:
    """Comment count from a channel post's linked discussion thread, if
    any (Telethon's Message.replies.replies) — 0 for posts with no
    comments or no linked discussion group."""
    replies = getattr(msg, "replies", None)
    return int(getattr(replies, "replies", 0) or 0)


def _media_type(msg) -> str:
    """"photo" | "video" | "video_note" (round/circle video) | "audio"
    (voice message or audio file) | "file" (any other document) | "" — used
    by the High-Quality Posts view (and the dashboard's recent-posts row)
    to pick a placeholder icon before a thumbnail is fetched (see
    app.tools.media_fetch). Checked in this order since a round video is
    technically also a "video", and a voice message/audio file/photo/video
    are all technically also a "document", in Telethon's eyes — the more
    specific convenience property always has to be checked first."""
    if getattr(msg, "video_note", None) is not None:
        return "video_note"
    if getattr(msg, "video", None) is not None:
        return "video"
    if getattr(msg, "photo", None) is not None:
        return "photo"
    if getattr(msg, "voice", None) is not None or getattr(msg, "audio", None) is not None:
        return "audio"
    if getattr(msg, "document", None) is not None:
        return "file"
    return ""


def _preview(text: str, limit: int = 140) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _top_ids(rows: list[dict], key: str, n: int) -> set[int]:
    ranked = sorted(rows, key=lambda r: r[key], reverse=True)
    return {r["id"] for r in ranked[:n]}


def _one_per_month_ids(rows: list[dict]) -> set[int]:
    """Best (highest-viewed) post's id from *every* calendar month that has
    any post at all. A flat "top-N most recent posts" cutoff (see
    `_top_ids(rows, "ts", top_n)` below) can be entirely consumed by just
    one or two unusually active recent months, silently dropping quieter
    months right next to them from the pool — this guarantees no month is
    ever completely unrepresented, regardless of how many posts its
    neighbors have."""
    by_month: dict[str, list[dict]] = {}
    for r in rows:
        label = (r.get("date") or "")[:7]
        if len(label) == 7:
            by_month.setdefault(label, []).append(r)
    ids: set[int] = set()
    for month_rows in by_month.values():
        ids.add(max(month_rows, key=lambda r: r["views"])["id"])
    return ids


def _trimmed_mean_drop_top(values: list[int], drop_frac: float) -> float:
    """Mean after dropping the top `drop_frac` fraction of values (sorted
    ascending) — a true trimmed mean over the *whole* distribution, not
    just excluding the single largest value. Used for avg_reposts_trimmed:
    reposts are far more top-heavy than views/reactions (one giveaway or
    forward chain can be a huge share of a channel's total repost count
    across its whole history), so the plain average swings on outliers
    much more than it should for "typical performance"."""
    if not values:
        return 0.0
    vals = sorted(values)
    keep_n = max(1, round(len(vals) * (1 - drop_frac)))
    kept = vals[:keep_n]
    return sum(kept) / len(kept)


async def _public_forwards(client, input_channel, msg_id: int) -> dict:
    """{'count': int, 'items': [{title, link, views}]} for one post, or
    {'count': -1, 'items': []} if stats aren't available for this channel."""
    from telethon.tl.functions.stats import GetMessagePublicForwardsRequest
    from telethon.tl.types import PublicForwardMessage

    try:
        res = await client(GetMessagePublicForwardsRequest(
            channel=input_channel, msg_id=msg_id, offset="", limit=100))
    except Exception:
        return {"count": -1, "items": []}

    chats_by_id = {c.id: c for c in getattr(res, "chats", [])}
    items: list[dict] = []
    for fwd in getattr(res, "forwards", []):
        if not isinstance(fwd, PublicForwardMessage):
            continue
        m = fwd.message
        cid = getattr(getattr(m, "peer_id", None), "channel_id", None)
        chat = chats_by_id.get(cid)
        title = str(getattr(chat, "title", None) or cid or "?")
        username = getattr(chat, "username", None)
        if username:
            link = f"https://t.me/{username}/{m.id}"
        elif cid:
            link = f"https://t.me/c/{cid}/{m.id}"
        else:
            link = ""
        items.append({"title": title, "link": link,
                      "views": int(getattr(m, "views", 0) or 0)})
    count = int(getattr(res, "count", len(items)) or len(items))
    return {"count": count, "items": items}


async def _count_since(client, entity, cutoff: datetime | None) -> int:
    """Server-computed post count for the period (cheap, no per-message fetch)."""
    if cutoff is None:
        return (await client.get_messages(entity, limit=0)).total or 0
    boundary = await client.get_messages(entity, limit=1, offset_date=cutoff)
    min_id = boundary[0].id if boundary else 0
    return (await client.get_messages(entity, limit=0, min_id=min_id)).total or 0


async def _channel_info(client, entity) -> dict:
    """Member count, creation date and description via GetFullChannel.

    Best-effort: a chat that isn't a broadcast channel (or a stats hiccup)
    just yields blanks rather than failing the whole fetch.
    """
    info = {
        "id": int(getattr(entity, "id", 0) or 0),
        "title": str(getattr(entity, "title", "") or ""),
        "username": getattr(entity, "username", None) or "",
        "members": 0,
        "about": "",
        "created": "",
    }
    created = getattr(entity, "date", None)
    if created:
        info["created"] = created.isoformat()
    try:
        from telethon.tl.functions.channels import GetFullChannelRequest
        full = await client(GetFullChannelRequest(channel=entity))
        fc = full.full_chat
        info["members"] = int(getattr(fc, "participants_count", 0) or 0)
        info["about"] = str(getattr(fc, "about", "") or "")
    except Exception:
        pass
    return info


def _monthly_aggregates(month_posts: list[dict], avg_views: float) -> dict[str, dict]:
    """Per-month sums over *every* scanned post (not just the top-N pool), so
    folder/period views reflect real totals instead of a sampled subset.

    `month_posts` must be the per-post snapshots captured by `_account` at
    merge time — not the final `rows` list, whose views/reactions/forwards
    get bumped up afterwards by later album members via max(). Reading from
    the mutated `rows` would make these sums inconsistent with `stats`
    (avg_views etc.), which are accumulated from the same snapshots.
    """
    agg: dict[str, dict] = {}
    for p in month_posts:
        a = agg.setdefault(p["label"], {"views": 0, "shares": 0, "reactions": 0, "viral_count": 0})
        a["views"] += p["views"]
        a["shares"] += p["shares"]
        a["reactions"] += p["reactions"]
        if avg_views and p["views"] > 2 * avg_views:
            a["viral_count"] += 1
    return agg


def _monthly_top_posts(rows: list[dict]) -> dict[str, dict]:
    """For each month, the single highest-viewed merged post — computed over
    *every* scanned post (final, post-album-merge state), not just the
    top-N pool, so a period's "most viewed post" is always the real one
    instead of only showing up when it happens to also be a global top-N
    pick."""
    top: dict[str, dict] = {}
    for r in rows:
        label = (r.get("date") or "")[:7]
        if len(label) != 7:
            continue
        cur = top.get(label)
        if cur is None or r["views"] > cur["views"]:
            top[label] = r
    return top


def _monthly_series(month_counts: dict[str, int], month_agg: dict[str, dict],
                    month_top: dict[str, dict]) -> list[dict]:
    """Fill gaps between the first and last month so the activity bars have no
    holes; each item is {'label': 'YYYY-MM', 'count', 'views', 'shares',
    'reactions', 'viral_count', 'top'}."""
    if not month_counts:
        return []
    keys = sorted(month_counts)
    (y0, m0), (y1, m1) = (map(int, keys[0].split("-")),
                          map(int, keys[-1].split("-")))
    series: list[dict] = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        label = f"{y:04d}-{m:02d}"
        a = month_agg.get(label, {})
        series.append({
            "label": label,
            "count": int(month_counts.get(label, 0)),
            "views": int(a.get("views", 0)),
            "shares": int(a.get("shares", 0)),
            "reactions": int(a.get("reactions", 0)),
            "viral_count": int(a.get("viral_count", 0)),
            "top": month_top.get(label),
        })
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return series


async def run_channel_stat(client, p: dict, ctx) -> str:
    """p: channel, top_n, period (PERIOD_DAYS key, '' = all), fetch_public."""
    top_n = int(p.get("top_n") or 20)
    period = p.get("period") or ""
    cutoff = period_cutoff(period)
    fetch_public = bool(p.get("fetch_public"))

    entity = await resolve_entity(client, p["channel"])
    info = await _channel_info(client, entity)
    title = info["title"] or str(p["channel"])
    try:
        total = await _count_since(client, entity, cutoff)
    except Exception:
        total = 0
    ctx.log(f"Scanning '{title}' ({total or 'unknown'} post(s))…")

    rows: list[dict] = []
    scanned = 0
    current: dict | None = None   # album row currently being accumulated
    current_gid = None
    current_anchor_msg = None     # first message of `current`'s group — its
                                  # date/media type represent the merged post
    err_cutoff = datetime.now(timezone.utc) - timedelta(days=ERR_MIN_AGE_DAYS)
    last_full_year = datetime.now(timezone.utc).year - 1

    # Activity accumulators (per merged post).
    posts = 0
    sum_views = 0
    views_n = 0
    max_views = 0
    sum_reactions = 0
    sum_forwards = 0
    max_forwards = 0
    sum_views_settled = 0
    views_settled_n = 0
    sum_views_last_year = 0
    sum_forwards_last_year = 0
    views_seen: list[int] = []  # per-post views, for the viral-share pass below
    forwards_seen: list[int] = []  # per-post reposts, for the trimmed-mean pass below
    with_media = with_photo = with_document = 0
    hour_dist = [0] * 24
    weekday_dist = [0] * 7
    month_counts: dict[str, int] = {}
    month_posts: list[dict] = []  # per-post snapshot at merge time, for _monthly_aggregates
    first_ts: int | None = None
    last_ts: int | None = None

    def _account(row: dict, msg) -> None:
        """Fold one *finalized* merged post into the activity stats.

        Called once a post's album group is fully closed, so `row`'s
        views/reactions/forwards are already the max() across every group
        member. Calling this at group-*start* instead (before later album
        members are merged in) undercounts reactions in particular: Telegram
        often reports the real reaction count on only one message of a
        media group, not the one iteration happens to visit first.
        """
        nonlocal posts, sum_views, views_n, max_views, sum_reactions
        nonlocal sum_forwards, max_forwards, sum_views_settled, views_settled_n
        nonlocal sum_views_last_year, sum_forwards_last_year
        nonlocal with_media, with_photo, with_document, first_ts, last_ts
        posts += 1
        v = row["views"]
        views_seen.append(v)
        if v > 0:
            sum_views += v
            views_n += 1
            if msg.date and msg.date < err_cutoff:
                sum_views_settled += v
                views_settled_n += 1
        max_views = max(max_views, v)
        sum_reactions += row["reactions"]
        sum_forwards += row["forwards"]
        forwards_seen.append(row["forwards"])
        max_forwards = max(max_forwards, row["forwards"])
        if msg.date and msg.date.year == last_full_year:
            sum_views_last_year += v
            sum_forwards_last_year += row["forwards"]
        if getattr(msg, "media", None) is not None:
            with_media += 1
        if getattr(msg, "photo", None) is not None:
            with_photo += 1
        if getattr(msg, "document", None) is not None:
            with_document += 1
        d = msg.date
        if d:
            hour_dist[d.hour] += 1
            weekday_dist[d.weekday()] += 1
            label = f"{d.year:04d}-{d.month:02d}"
            month_counts[label] = month_counts.get(label, 0) + 1
            month_posts.append({"label": label, "views": v,
                                "shares": row["forwards"], "reactions": row["reactions"]})
            ts = int(d.timestamp())
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)

    async for msg in client.iter_messages(entity):
        if ctx.cancelled():
            break
        if cutoff and msg.date and msg.date < cutoff:
            break  # newest -> oldest, so we're past the window
        scanned += 1
        if getattr(msg, "action", None) is not None:
            continue  # skip service messages (joins, pins, …)

        gid = getattr(msg, "grouped_id", None)
        full_text = " ".join((getattr(msg, "message", "") or "").split())
        text = _preview(full_text)
        views = int(getattr(msg, "views", 0) or 0)
        reactions = _reaction_total(msg)
        forwards = int(getattr(msg, "forwards", 0) or 0)
        comments = _comment_total(msg)

        if gid is not None and gid == current_gid:
            # Same album — merge into the row being built (see channel_top).
            current["ids"].append(msg.id)
            current["id"] = msg.id
            current["views"] = max(current["views"], views)
            current["reactions"] = max(current["reactions"], reactions)
            current["forwards"] = max(current["forwards"], forwards)
            current["comments"] = max(current["comments"], comments)
            if not current["text"] and text:
                current["text"] = text
                current["full_text"] = full_text
        else:
            if current is not None:
                _account(current, current_anchor_msg)  # previous group is now closed
            current = {
                "id": msg.id,
                "ids": [msg.id],
                "ts": int(msg.date.timestamp()) if msg.date else 0,
                "date": msg.date.isoformat() if msg.date else "",
                "text": text,
                "full_text": full_text,
                "views": views,
                "reactions": reactions,
                "forwards": forwards,
                "comments": comments,
                # From the anchor message only, like date/ts — an album's
                # other members aren't re-checked, matching the "cover"
                # item a viewer would actually see first.
                "media_type": _media_type(msg),
                "public": None,
            }
            current_gid = gid
            current_anchor_msg = msg
            rows.append(current)

        if scanned % HEARTBEAT_EVERY == 0:
            ctx.log(f"  scanned {scanned}/{total or '?'}…")
        ctx.progress(scanned, total)

    if current is not None:
        _account(current, current_anchor_msg)  # last group in the stream

    ctx.log(f"Read stats for {len(rows)} post(s) ({scanned} message(s) scanned).")

    # -------- activity summary --------
    if first_ts is not None and last_ts is not None:
        total_days = max(1, (last_ts - first_ts) // 86400 + 1)
        first_iso = datetime.fromtimestamp(first_ts, timezone.utc).isoformat()
        last_iso = datetime.fromtimestamp(last_ts, timezone.utc).isoformat()
    else:
        total_days, first_iso, last_iso = 0, "", ""
    avg_views_raw = sum_views / views_n if views_n else 0
    viral_count = sum(1 for v in views_seen if v > 2 * avg_views_raw) if avg_views_raw else 0
    stats = {
        "total_posts": posts,
        "avg_views": round(sum_views / views_n, 1) if views_n else 0,
        "viral_post_share": round(viral_count / posts * 100, 1) if posts else 0,
        "max_views": max_views,
        "avg_reactions": round(sum_reactions / posts, 1) if posts else 0,
        "avg_reposts": round(sum_forwards / posts, 1) if posts else 0,
        # Top 10% (by reposts) dropped before averaging — see
        # _trimmed_mean_drop_top. Used where a plain average would swing
        # too much on one-off repost spikes (e.g. Content Quality Index).
        "avg_reposts_trimmed": round(_trimmed_mean_drop_top(forwards_seen, 0.10), 2),
        "max_reposts": max_forwards,
        "avg_views_settled": round(sum_views_settled / views_settled_n, 1)
                             if views_settled_n else 0,
        "last_full_year": last_full_year,
        "last_year_views": sum_views_last_year,
        "last_year_reposts": sum_forwards_last_year,
        "avg_posts_per_day": round(posts / total_days, 2) if total_days else 0,
        "posts_with_media": with_media,
        "posts_with_photo": with_photo,
        "posts_with_document": with_document,
        "media_pct": round(with_media / posts * 100, 1) if posts else 0,
        "total_days": total_days,
        "first_post_date": first_iso,
        "last_post_date": last_iso,
    }
    month_agg = _monthly_aggregates(month_posts, avg_views_raw)
    month_top = _monthly_top_posts(rows)
    distributions = {
        "hour": hour_dist,
        "weekday": weekday_dist,          # Mon..Sun
        "monthly": _monthly_series(month_counts, month_agg, month_top),
    }

    # -------- engagement pool (union of top-N by each metric) --------
    # Also unions in the top-N most *recent* posts ("ts") and one post per
    # calendar month, not just the top-N by engagement — a fresh post has
    # barely had time to accumulate views/reactions/forwards, so on an
    # active channel with a long history it will almost never rank in the
    # engagement-based top-N, leaving the most recent weeks/months
    # completely unrepresented in the pool. "ts" alone isn't quite enough
    # either: a couple of unusually active recent months can consume the
    # entire top-N-by-recency budget and still leave a quieter month right
    # next to them with zero posts in the pool, which is where
    # _one_per_month_ids comes in — see its docstring. The High-Quality
    # Posts view, and the dashboard's "recent posts" row/Quality trend
    # line, all read from this same pool — without both of these, they'd
    # silently drop whichever recent months don't happen to stand out.
    pool_ids: set[int] = set()
    for key in ("views", "reactions", "forwards", "ts"):
        pool_ids |= _top_ids(rows, key, top_n)
    pool_ids |= _one_per_month_ids(rows)
    pool = [r for r in rows if r["id"] in pool_ids]
    pool.sort(key=lambda r: r["views"], reverse=True)

    if fetch_public and pool and not ctx.cancelled():
        input_channel = await client.get_input_entity(entity)
        ctx.log(f"Fetching public reposts for {len(pool)} post(s)…")
        unavailable = False
        for i, r in enumerate(pool, 1):
            if ctx.cancelled():
                break
            r["public"] = await _public_forwards(client, input_channel, r["id"])
            if r["public"]["count"] < 0:
                unavailable = True
            ctx.progress(i, len(pool))
        if unavailable:
            ctx.log("  Public-repost stats are unavailable for this channel "
                    "(needs a channel where you can view statistics).")

    username = info["username"]
    if username:
        link = f"https://t.me/{username}"
    elif info["id"]:
        link = f"https://t.me/c/{info['id']}"
    else:
        link = ""

    return json.dumps({
        "schema": 1,
        "cancelled": ctx.cancelled(),
        "channel": str(p["channel"]),
        "title": title,
        "username": username,
        "link": link,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "period": period,
        "top_n": top_n,
        "fetch_public": fetch_public,
        "info": info,
        "stats": stats,
        "distributions": distributions,
        "scanned": len(rows),
        "rows": pool,
    })
