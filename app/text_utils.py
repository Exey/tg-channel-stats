"""Small text helpers shared across UI modules that aren't specific to any
one domain (folders, tags, …)."""
from __future__ import annotations

_VOWELS = set("AEIOU" "АЕЁИОУЫЭЮЯ")


def consonant_abbreviation(text: str, n: int) -> str:
    """First `n` consonant letters of `text`, uppercased — digits, spaces
    and punctuation are skipped entirely, e.g. "Photographers" (n=2) ->
    "PH", "Крафт" (n=3) -> "КРФ". Falls back to the first `n` letters as-is
    (vowels included) when the word doesn't have `n` consonants to begin
    with, e.g. "Raw" (n=3) only has 2 consonants (R, W) so it stays "RAW"
    whole rather than truncating to "RW"."""
    letters = [ch for ch in text.upper() if ch.isalpha()]
    consonants = [ch for ch in letters if ch not in _VOWELS]
    if len(consonants) >= n:
        return "".join(consonants[:n])
    return "".join(letters[:n])
