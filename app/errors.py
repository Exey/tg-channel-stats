"""Friendly, user-facing text for exceptions the app can't fully prevent —
paired with main.py's global excepthook, which is what actually puts this
text in front of the user. Kept separate from that install-time wiring so
any call site that catches its own OSError (an export/save button, for
instance) can reuse the same wording instead of just dumping str(exc).
"""
from __future__ import annotations

import errno


def friendly_os_error(exc: OSError) -> str:
    """User-facing message for a failed read/write — calls out "disk full"
    specifically (errno 28 / ENOSPC), the overwhelmingly most common real
    -world cause and the one with an obvious, actionable fix; falls back
    to the raw OS message for anything else (permissions, missing device,
    etc.), which is still more useful than a generic "something went
    wrong"."""
    if exc.errno == errno.ENOSPC:
        return ("Your disk is almost full, so this couldn't be saved.\n\n"
                "Free up some space — empty the Trash, clear out old "
                "Downloads, or check what's using space under "
                "⌘ About This Mac → Storage — then try again.")
    return str(exc)
