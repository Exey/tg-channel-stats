"""Helpers shared by all tools."""
from __future__ import annotations

import asyncio
import os


async def retry(ctx, coro, *args, **kwargs):
    """Call coro(*args, **kwargs) with FloodWait / transient-error retries."""
    from telethon import errors

    for attempt in range(10):
        if ctx.cancelled():
            return None
        try:
            return await coro(*args, **kwargs)
        except errors.FloodWaitError as e:
            ctx.log(f"  FloodWait: sleeping {e.seconds}s…")
            await _sleep_cancellable(ctx, e.seconds)
        except (ConnectionError, OSError, asyncio.TimeoutError) as e:
            wait = 5 * (attempt + 1)
            ctx.log(f"  Transient error: {e}. Retrying in {wait}s "
                    f"({attempt + 1}/10)…")
            await _sleep_cancellable(ctx, wait)
    ctx.log("  Giving up after 10 attempts.")
    return None


async def _sleep_cancellable(ctx, seconds: float) -> None:
    end = asyncio.get_event_loop().time() + seconds
    while asyncio.get_event_loop().time() < end:
        if ctx.cancelled():
            return
        await asyncio.sleep(min(0.5, seconds))


async def resolve_entity(client, value):
    """Accepts @username, t.me link, or numeric ID (incl. -100… form).

    Raises ValueError with an actionable message if the chat can't be found —
    either because the ID is wrong, or because Telethon has never seen that
    peer before (it can't resolve a bare numeric ID unless it's cached the
    access_hash from a prior dialog/message) and this account isn't a member.
    """
    from telethon.tl.types import PeerChannel

    v = str(value).strip()
    if not v:
        raise ValueError("Empty channel/chat identifier")
    try:
        num = int(v)
    except ValueError:
        try:
            return await client.get_entity(v)
        except ValueError as e:
            raise ValueError(
                f"Could not find chat '{v}'. Check the @username/link is "
                f"correct and that this account can see that chat."
            ) from e

    try:
        return await client.get_entity(num)
    except Exception:
        pass
    channel_id = int(str(num)[4:]) if str(num).startswith("-100") else num
    try:
        return await client.get_entity(PeerChannel(channel_id))
    except Exception:
        pass

    # Last resort: get_entity() only resolves peers Telethon already has an
    # access_hash for. Scanning dialogs forces a fresh sync from the
    # account's own chat list, which also covers IDs typed with the wrong
    # -100 prefix/sign.
    async for dialog in client.iter_dialogs():
        if dialog.id == num or getattr(dialog.entity, "id", None) == channel_id:
            return dialog.entity

    raise ValueError(
        f"Chat ID '{value}' not found or not accessible with this account. "
        f"Double-check the ID (e.g. via @userinfobot or web.telegram.org) and "
        f"make sure this account is a member of that chat."
    )


def read_progress(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


def save_progress(path: str, value: int) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(value))


def reset_progress(path: str) -> bool:
    if os.path.exists(path):
        os.remove(path)
        return True
    return False
