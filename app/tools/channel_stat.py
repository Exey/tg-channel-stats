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


def _preview(text: str, limit: int = 140) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _top_ids(rows: list[dict], key: str, n: int) -> set[int]:
    ranked = sorted(rows, key=lambda r: r[key], reverse=True)
    return {r["id"] for r in ranked[:n]}


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


def _monthly_series(month_counts: dict[str, int]) -> list[dict]:
    """Fill gaps between the first and last month so the activity bars have no
    holes; each item is {'label': 'YYYY-MM', 'count': n}."""
    if not month_counts:
        return []
    keys = sorted(month_counts)
    (y0, m0), (y1, m1) = (map(int, keys[0].split("-")),
                          map(int, keys[-1].split("-")))
    series: list[dict] = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        label = f"{y:04d}-{m:02d}"
        series.append({"label": label, "count": int(month_counts.get(label, 0))})
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
    with_media = with_photo = with_document = 0
    hour_dist = [0] * 24
    weekday_dist = [0] * 7
    month_counts: dict[str, int] = {}
    first_ts: int | None = None
    last_ts: int | None = None

    def _account(row: dict, msg) -> None:
        """Fold one *new* merged post into the activity stats."""
        nonlocal posts, sum_views, views_n, max_views, sum_reactions
        nonlocal sum_forwards, max_forwards, sum_views_settled, views_settled_n
        nonlocal sum_views_last_year, sum_forwards_last_year
        nonlocal with_media, with_photo, with_document, first_ts, last_ts
        posts += 1
        v = row["views"]
        if v > 0:
            sum_views += v
            views_n += 1
            if msg.date and msg.date < err_cutoff:
                sum_views_settled += v
                views_settled_n += 1
        max_views = max(max_views, v)
        sum_reactions += row["reactions"]
        sum_forwards += row["forwards"]
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
            month_counts[f"{d.year:04d}-{d.month:02d}"] = \
                month_counts.get(f"{d.year:04d}-{d.month:02d}", 0) + 1
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

        if gid is not None and gid == current_gid:
            # Same album — merge into the row being built (see channel_top).
            current["ids"].append(msg.id)
            current["id"] = msg.id
            current["views"] = max(current["views"], views)
            current["reactions"] = max(current["reactions"], reactions)
            current["forwards"] = max(current["forwards"], forwards)
            if not current["text"] and text:
                current["text"] = text
                current["full_text"] = full_text
        else:
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
                "public": None,
            }
            current_gid = gid
            rows.append(current)
            _account(current, msg)

        if scanned % HEARTBEAT_EVERY == 0:
            ctx.log(f"  scanned {scanned}/{total or '?'}…")
        ctx.progress(scanned, total)

    ctx.log(f"Read stats for {len(rows)} post(s) ({scanned} message(s) scanned).")

    # -------- activity summary --------
    if first_ts is not None and last_ts is not None:
        total_days = max(1, (last_ts - first_ts) // 86400 + 1)
        first_iso = datetime.fromtimestamp(first_ts, timezone.utc).isoformat()
        last_iso = datetime.fromtimestamp(last_ts, timezone.utc).isoformat()
    else:
        total_days, first_iso, last_iso = 0, "", ""
    stats = {
        "total_posts": posts,
        "avg_views": round(sum_views / views_n, 1) if views_n else 0,
        "max_views": max_views,
        "avg_reactions": round(sum_reactions / posts, 1) if posts else 0,
        "avg_reposts": round(sum_forwards / posts, 1) if posts else 0,
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
    distributions = {
        "hour": hour_dist,
        "weekday": weekday_dist,          # Mon..Sun
        "monthly": _monthly_series(month_counts),
    }

    # -------- engagement pool (union of top-N by each metric) --------
    pool_ids: set[int] = set()
    for key in ("views", "reactions", "forwards"):
        pool_ids |= _top_ids(rows, key, top_n)
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
