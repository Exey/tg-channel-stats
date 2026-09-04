"""Mentions: person names found in post texts, reconciled into one canonical
identity per person across channels.

Unlike app.tags (an *external* .md file, only reloaded into the app, never
written by it), `mentions.md` is app-owned and live-edited from the Mentions
view (app.ui.compare.mentions_view) — MentionsStore both reads and writes it,
the same way app.folders/app.tags own their JSON files, just Markdown-shaped
so it stays human-readable/diffable. Table shape:

    | id | names | unclear links |

- **id** — a channel `@username` or a full name (ФИО), the canonical
  identity this row represents.
- **names** — every name-variant text extracted from post captions that's
  been linked to this id (nicknames, declined forms, etc.), comma-separated.
- **unclear links** — `t.me/...` links to posts where the name showed up in
  a form too ambiguous to confidently attach (comma-separated), left for a
  human to resolve later.

Extraction itself (extract_person_names) is a thin wrapper around
mawo-slovnet's NewsNERTagger — a Russian NER model (PER/LOC/ORG spans).
Other things tried here, each ruled out for a concrete, verified reason
rather than assumed from docs/snippets:

- mawo-natasha — NamesExtractor is an unconditional stub, and its own NER
  path points at a dead model-download URL.
- DeepPavlov — its latest release pins numpy<1.24, which has no Python 3.12
  wheels (a failed install, not just its docs, which separately claim
  support only through 3.10/3.11 anyway).
- transformers running Babelscape/wikineural-multilingual-ner — genuinely
  caught more real names (e.g. "Марина", "Мария Рязанская", both missed by
  mawo-slovnet) but at the cost of a ~640MB model plus a torch dependency
  that made a packaged build balloon from ~90MB to ~726MB — not a trade
  worth it here, see find_known_names_in_text below for a lighter partial
  remedy instead.
- genuine natasha's NamesExtractor (rule-based, not the same as its NER
  path) — badly over-triggers on ordinary vocabulary: on the exact test
  sentences used to evaluate every option here, it tagged "и" (and),
  "без" (without), "Просто" (just) and a verb as name components. Its
  suggested "expand the dictionary" pipeline (CustomGrammemesPipeline,
  Combinator) doesn't exist in yargy or natasha's actual API either —
  checked directly against both packages' exports, not assumed.

Imported lazily and wrapped in a broad except so a channel without the
dependency installed, or a first-run model download that fails, degrades
to "no names found" instead of crashing the view.

find_known_names_in_text is the "lighter partial remedy" mentioned above: a
plain-Python supplemental pass, no model and no new dependency, that
catches a post mentioning someone already in mentions.md even when the NER
model doesn't tag that mention as PER at all (its most common failure
mode — a bare first name in a short, casual sentence). It doesn't help
with a person NOT already in mentions.md; extract_person_names is still
what finds those in the first place.

NameExceptions is the opposite direction — a blocklist (name_exceptions.txt,
alongside mentions.md, plain text, one entry per line) of things
mawo-slovnet or find_known_names_in_text found that plainly aren't a
person, e.g. "Мастер-класс" ("master class", a compound noun the NER model
has tagged as PER before). Declension-aware the same way
MentionsStore.find_row is (see _names_declension_match), so one entry
covers every grammatical form. Pre-seeded with entries found during
development; the Mentions view's Names Found table also offers an
**Ignore** action next to **Link…** to add to it directly, for whatever
the pre-seeded list doesn't already cover.

Case/declension normalization (_ru_lemma, used by MentionsStore.find_row's
third-priority match tier) is a thin wrapper around pymorphy3 — a real
morphological analyzer, picked over the hand-rolled case-ending-suffix list
this module used before it (still here as _ru_stem's fallback for when
pymorphy3 isn't installed): pymorphy3 correctly lemmatizes adjectival
surnames (e.g. "Рязанской" -> "рязанский") that a fixed suffix list can't,
and — unlike pymorphy2 — has no pkg_resources import, so it doesn't need
the setuptools<81 pin pymorphy2 (and genuine natasha, which uses pymorphy2
internally) would need on a modern setuptools.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path

from .config import config_dir

logger = logging.getLogger(__name__)


def mentions_path() -> Path:
    return config_dir() / "mentions.md"


# --------------------------------------------------------------- md i/o
def _split_cell(cell: str) -> list[str]:
    """Comma-separated cell -> a clean, order-preserving, de-duplicated list."""
    seen: dict[str, None] = {}
    for part in cell.split(","):
        part = part.strip()
        if part:
            seen.setdefault(part, None)
    return list(seen)


def _join_cell(values: list[str]) -> str:
    return ", ".join(v.strip() for v in values if v.strip())


def parse_md(text: str) -> list[dict]:
    """[{"id","names","links"}, …] from a "| id | names | unclear links |"
    table — tolerant of extra whitespace, skips the header/divider rows and
    any data row with an empty id. Same forgiving shape as app.tags's
    parse_md_table."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-+:?", c or "-") for c in cells):
            continue  # divider row
        rows.append(cells)
    if not rows:
        return []
    out = []
    for cells in rows[1:]:
        cells += [""] * (3 - len(cells))
        id_ = cells[0].strip()
        if not id_:
            continue
        out.append({"id": id_, "names": _split_cell(cells[1]), "links": _split_cell(cells[2])})
    return out


# Tokenizes on Unicode letters (hyphen kept inside a word, so "Мастер-класс"
# stays one token) — used everywhere two names get compared word-by-word,
# instead of plain .split(), specifically so a decorative emoji glued
# straight onto a word with no space ("Курилко🔥", a common casual-writing
# style) doesn't become part of that word and break what would otherwise be
# an exact match.
_WORD_RE = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*", re.UNICODE)


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def _word_substring_match(candidate: str, needle: str) -> bool:
    """Whether (already casefolded) `candidate`'s words appear as a
    contiguous run inside `needle`'s — e.g. "лина жу" inside "мастер-класс
    лина жу" (NER grabbing extra text around a real name). Matched at word
    boundaries, not raw character containment — "иван" must not match
    inside "иванов" just because it happens to be a substring of those
    characters; it's a different word (a surname), not extra text around
    "иван"."""
    cwords, nwords = _words(candidate), _words(needle)
    if not cwords or len(cwords) > len(nwords):
        return False
    return any(nwords[i:i + len(cwords)] == cwords
              for i in range(len(nwords) - len(cwords) + 1))


def render_md(rows: list[dict]) -> str:
    lines = ["| id | names | unclear links |", "| --- | --- | --- |"]
    for row in rows:
        id_ = row.get("id", "").replace("|", "\\|")
        names = _join_cell(row.get("names") or []).replace("|", "\\|")
        links = _join_cell(row.get("links") or []).replace("|", "\\|")
        lines.append(f"| {id_} | {names} | {links} |")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------- Russian declension
# Primary: pymorphy3, a real morphological analyzer (dictionary-backed,
# gender/declension-class aware — correctly lemmatizes an adjectival
# surname like "Рязанской" to "рязанский", which no fixed suffix list would
# get right). Picked over pymorphy2 (and genuine natasha, which uses
# pymorphy2 internally) because pymorphy2 imports pkg_resources, gone from
# setuptools>=81 — pymorphy3 doesn't need that pin. Lazy-imported and
# wrapped in a broad except, same shape as _get_ner_pipeline below, so a
# machine without it installed falls back to _ru_stem: a curated list of
# common personal-name case endings, good enough for e.g.
# "Алисой"/"Алисы"/"Алисе" -> "Алиса" but not adjectival surnames.
_RU_CASE_SUFFIXES = (
    "иями", "ями", "ами", "ой", "ей", "ою", "ею",
    "ом", "ем", "им", "ым", "ах", "ях",
    "а", "я", "ы", "и", "е", "у", "ю", "й", "ь",
)

_morph = None
_morph_failed = False


def _get_morph():
    global _morph, _morph_failed
    if _morph is not None or _morph_failed:
        return _morph
    try:
        from pymorphy3 import MorphAnalyzer
        _morph = MorphAnalyzer()
    except Exception:  # noqa: BLE001 - missing/broken dependency falls back to _ru_stem
        logger.warning("pymorphy3 unavailable; falling back to the case-ending "
                       "heuristic for declension matching", exc_info=True)
        _morph_failed = True
        _morph = None
    return _morph


def _ru_stem(word: str) -> str:
    """Best-effort case-ending strip for one (already casefolded) word —
    never below 2 characters, so a short word isn't stripped to nothing.
    Only reached when pymorphy3 isn't installed (see _ru_lemmas)."""
    for suf in _RU_CASE_SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 2:
            return word[:-len(suf)]
    return word


_RU_LEMMA_MIN_SCORE = 0.1


def _ru_lemmas(word: str) -> set[str]:
    """Dictionary forms pymorphy3 considers plausible for (already
    casefolded) `word`, scored at least _RU_LEMMA_MIN_SCORE — a set, not
    just the top guess, because pymorphy3 ranks genuinely ambiguous forms
    as ties (e.g. "марину" is equally likely the dative of the rare masc.
    name "Марин" or the accusative of "Марина", 0.5/0.5 — both come back
    here). The score floor is what keeps this from reintroducing the
    "иван"/"иванов" false positive that word-substring matching had before
    it was fixed: pymorphy3 does have a real parse of "иванов" as a rare
    plural form of "иван" ("у нас пять Иванов"), just at ~1% likelihood
    against the ~96% surname reading, so it's excluded by the floor.
    {_ru_stem(word)} if pymorphy3 isn't installed or fails on this word."""
    morph = _get_morph()
    if morph is None:
        return {_ru_stem(word)}
    try:
        parses = morph.parse(word)
        lemmas = {p.normal_form for p in parses if p.score >= _RU_LEMMA_MIN_SCORE}
        return lemmas or {p.normal_form for p in parses} or {word}
    except Exception:  # noqa: BLE001 - a single bad word shouldn't break a match
        return {_ru_stem(word)}


def _names_declension_match(a: str, b: str) -> bool:
    """Whether (already casefolded) `a` and `b` are plausibly the same
    (possibly multi-word) name in different grammatical cases — e.g. "иван
    петров" vs "ивана петрова". Word-by-word, same word count required, each
    pair either identical or sharing at least one dictionary form (see
    _ru_lemmas). Deliberately conservative — real lemmas, not fuzzy prefix
    closeness — so e.g. "иван" (a first name) doesn't collide with "иванов"
    (a whole different word, a surname, not a case form of the first
    name)."""
    wa, wb = _words(a), _words(b)
    if not wa or len(wa) != len(wb):
        return False
    return all(
        x == y or (len(x) >= 3 and len(y) >= 3 and _ru_lemmas(x) & _ru_lemmas(y))
        for x, y in zip(wa, wb))


# ------------------------------------------------------------------ store
class MentionsStore:
    """In-memory rows plus a dirty flag — the Mentions view edits this
    directly and calls save() (explicitly, or automatically on leaving the
    view); nothing here talks to Qt."""

    def __init__(self) -> None:
        self.path = mentions_path()
        self.rows: list[dict] = []
        self.dirty = False
        self.load()

    def load(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            self.rows = []
        else:
            self.rows = parse_md(text)
        self.dirty = False

    def save(self) -> None:
        if not self.dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = render_md(self.rows)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        self.dirty = False

    # ------------------------------------------------------------- lookup
    def find_row(self, name: str) -> dict | None:
        """The row (if any) whose id or a names-variant case-insensitively
        matches `name` — exactly; failing that, contained in `name` as a
        whole-word run (see _word_substring_match — mawo-slovnet's NER
        sometimes grabs extra text around a real name, e.g. extracting
        "Мастер-класс Лина Жу" when mentions.md already has "Лина Жу");
        failing that, a plausible Russian case variant (see
        _names_declension_match — "Алиса" in mentions.md matches an
        extraction of "Алисой" or "Алисы") — in that priority order,
        most-confident match first. What the Mentions view's per-channel
        extracted-names table uses to show its found/linked indicator.
        Substring candidates under 4 characters are skipped so a short
        fragment can't spuriously match unrelated text."""
        needle = name.strip().casefold()
        if not needle:
            return None
        for row in self.rows:
            if row.get("id", "").strip().casefold() == needle:
                return row
            if any(n.strip().casefold() == needle for n in row.get("names") or []):
                return row
        for row in self.rows:
            candidates = [row.get("id", "")] + list(row.get("names") or [])
            for c in candidates:
                c = c.strip().casefold()
                if len(c) >= 4 and _word_substring_match(c, needle):
                    return row
        for row in self.rows:
            candidates = [row.get("id", "")] + list(row.get("names") or [])
            for c in candidates:
                c = c.strip().casefold()
                if c and _names_declension_match(c, needle):
                    return row
        return None

    # ------------------------------------------------------------- edits
    def add_row(self, id_: str, names: list[str] | None = None,
               links: list[str] | None = None) -> dict:
        row = {"id": id_, "names": list(names or []), "links": list(links or [])}
        self.rows.append(row)
        self.dirty = True
        return row

    def attach_name(self, row: dict, name: str) -> None:
        name = name.strip()
        if name and name.casefold() not in (n.casefold() for n in row["names"]):
            row["names"].append(name)
            self.dirty = True

    def attach_link(self, row: dict, link: str) -> None:
        link = link.strip()
        if link and link not in row["links"]:
            row["links"].append(link)
            self.dirty = True

    def remove_row(self, row: dict) -> None:
        if row in self.rows:
            self.rows.remove(row)
            self.dirty = True


# -------------------------------------------------------------- extraction
_tagger = None
_tagger_failed = False


def _get_tagger():
    global _tagger, _tagger_failed
    if _tagger is not None or _tagger_failed:
        return _tagger
    try:
        from mawo_slovnet import NewsNERTagger
        _tagger = NewsNERTagger()
    except Exception:  # noqa: BLE001 - missing/broken dependency degrades to no names
        logger.warning("mawo-slovnet unavailable; name extraction disabled", exc_info=True)
        _tagger_failed = True
        _tagger = None
    return _tagger


def extraction_available() -> bool:
    return _get_tagger() is not None


def extract_person_names(text: str) -> list[str]:
    """Distinct PER-span substrings NewsNERTagger finds in `text`, in the
    order first seen — raw as tagged, not case- or morphology-normalized
    (that's what linking a variant into a MentionsStore row, or the
    declension-aware match tier in MentionsStore.find_row, is for). Empty
    list if extraction isn't available or the text is blank."""
    text = (text or "").strip()
    if not text:
        return []
    tagger = _get_tagger()
    if tagger is None:
        return []
    try:
        markup = tagger(text)
    except Exception:  # noqa: BLE001 - a single bad post shouldn't break a scan
        logger.warning("NER tagging failed for one post", exc_info=True)
        return []
    seen: dict[str, None] = {}
    for span in getattr(markup, "spans", []) or []:
        if getattr(span, "type", None) != "PER":
            continue
        name = text[span.start:span.stop].strip()
        if name:
            seen.setdefault(name, None)
    return list(seen)


# ------------------------------------------------ known-name dictionary scan
def find_known_names_in_text(text: str, known: list[str]) -> list[str]:
    """Every one of `known` (id/names candidates from mentions.md) that
    shows up in `text` as a contiguous word run — exactly, or as a
    plausible Russian case variant of every word in it (see
    _names_declension_match) — returned spelled the way `text` actually has
    it, not the way `known` does. A supplemental pass alongside NER
    extraction (extract_person_names): this doesn't need the model to have
    tagged the mention as PER at all, which covers NER's most common miss
    here — a bare first name in a short, casual sentence. It can only ever
    confirm a name mentions.md already knows about; extract_person_names is
    still what finds a person nobody's added yet."""
    words = _words(text)
    if not words:
        return []
    cwords = [w.casefold() for w in words]
    found: dict[str, None] = {}
    for name in known:
        needle = [w.casefold() for w in _words(name)]
        if not needle:
            continue
        n = len(needle)
        for i in range(len(cwords) - n + 1):
            window = cwords[i:i + n]
            if window == needle or all(
                    x == y or (len(x) >= 3 and len(y) >= 3 and _ru_lemmas(x) & _ru_lemmas(y))
                    for x, y in zip(window, needle)):
                found.setdefault(" ".join(words[i:i + n]), None)
                break
    return list(found)


# ---------------------------------------------------------- name exceptions
def name_exceptions_path() -> Path:
    return config_dir() / "name_exceptions.txt"


# Known NER false positives found during development — always checked,
# even on a fresh install with no name_exceptions.txt of its own yet (see
# NameExceptions.contains). "Мастер-класс" ("master class") is a compound
# noun mawo-slovnet has tagged as PER before.
_DEFAULT_NAME_EXCEPTIONS = {
    "мастер-класс",
}


class NameExceptions:
    """A small, flat, declension-aware blocklist of text that should never
    be treated as a person name — the Mentions view's Names Found table
    offers an **Ignore** action (next to **Link…**) that adds to this
    directly. Plain text, one entry per line, not a Markdown table like
    mentions.md/tags.md — there's nothing here but the blocked text itself.
    Unlike MentionsStore, there's no separate Save step: an add() takes
    effect (and persists) immediately, the same way clicking Ignore should
    feel — there's no meaningful "unsaved" state for a one-directional
    blocklist to sit in."""

    def __init__(self) -> None:
        self.path = name_exceptions_path()
        self._items: set[str] = set()
        self.load()

    def load(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            self._items = set()
            return
        self._items = {line.strip().casefold() for line in text.splitlines() if line.strip()}

    def contains(self, name: str) -> bool:
        """Whether `name` matches an exception exactly or as a plausible
        Russian case variant (see _names_declension_match) — checked
        against both the built-in defaults and whatever's been added to
        name_exceptions.txt."""
        needle = name.strip().casefold()
        if not needle:
            return False
        all_items = _DEFAULT_NAME_EXCEPTIONS | self._items
        if needle in all_items:
            return True
        return any(_names_declension_match(item, needle) for item in all_items)

    def filter(self, names: list[str]) -> list[str]:
        return [n for n in names if not self.contains(n)]

    def add(self, name: str) -> None:
        name = name.strip()
        if not name or self.contains(name):
            return
        self._items.add(name.casefold())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = sorted(self._items)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + ("\n" if lines else ""))
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
