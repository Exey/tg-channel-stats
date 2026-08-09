"""Month/season period-key helpers shared by the dashboard trend chart and
the Folder Stats view, so both group `distributions.monthly` entries into
the same season buckets. Also the "Year mode" rolling-window options shared
by Folder Stats and High-Quality Posts (see YEAR_WINDOW_OPTIONS)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

SEASON_BY_MONTH = {
    12: "Winter", 1: "Winter", 2: "Winter",
    3: "Spring", 4: "Spring", 5: "Spring",
    6: "Summer", 7: "Summer", 8: "Summer",
    9: "Fall", 10: "Fall", 11: "Fall",
}
SEASON_ORDER = {"Winter": 0, "Spring": 1, "Summer": 2, "Fall": 3}


def period_key_label(year: int, month: int, mode: str) -> tuple[tuple, str]:
    """(sort_key, display_label) for a calendar month, grouped into a season
    when mode == "season", a calendar half (Jan-Jun / Jul-Dec) when
    mode == "halfyear", else the month itself."""
    if mode == "season":
        season = SEASON_BY_MONTH[month]
        year = year + 1 if month == 12 else year
        if season == "Winter":
            label = f"Winter {year - 1}/{str(year)[2:]}"
        else:
            label = f"{season} {year}"
        return (year, SEASON_ORDER[season]), label
    if mode == "halfyear":
        half = 1 if month <= 6 else 2
        return (year, half), f"{year} H{half}"
    return (year, month), f"{year:04d}-{month:02d}"


# "Year mode" sub-options: (key, i18n label key, rolling window in days —
# None for "All Fetched Time", i.e. no cutoff at all). A flat rolling
# window from today, not a calendar year — "Last Year" means the last 365
# days, not e.g. all of 2026.
YEAR_WINDOW_OPTIONS = [
    ("half", "period_year_half", 182),
    ("1y", "period_year_1y", 365),
    ("1.5y", "period_year_1_5y", 547),
    ("2y", "period_year_2y", 730),
    ("all", "period_year_all", None),
]


def year_window_cutoff(window_days: int | None) -> datetime | None:
    """UTC cutoff datetime for a Year-mode window's day count, or None for
    no cutoff (the "All Fetched Time" option)."""
    if window_days is None:
        return None
    return datetime.now(timezone.utc) - timedelta(days=window_days)
