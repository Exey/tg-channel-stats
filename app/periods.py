"""Month/season period-key helpers shared by the dashboard trend chart and
the Folder Stats view, so both group `distributions.monthly` entries into
the same season buckets."""
from __future__ import annotations

SEASON_BY_MONTH = {
    12: "Winter", 1: "Winter", 2: "Winter",
    3: "Spring", 4: "Spring", 5: "Spring",
    6: "Summer", 7: "Summer", 8: "Summer",
    9: "Fall", 10: "Fall", 11: "Fall",
}
SEASON_ORDER = {"Winter": 0, "Spring": 1, "Summer": 2, "Fall": 3}


def period_key_label(year: int, month: int, mode: str) -> tuple[tuple, str]:
    """(sort_key, display_label) for a calendar month, grouped into a season
    when mode == "season" (else the month itself)."""
    if mode == "season":
        season = SEASON_BY_MONTH[month]
        year = year + 1 if month == 12 else year
        if season == "Winter":
            label = f"Winter {year - 1}/{str(year)[2:]}"
        else:
            label = f"{season} {year}"
        return (year, SEASON_ORDER[season]), label
    return (year, month), f"{year:04d}-{month:02d}"
