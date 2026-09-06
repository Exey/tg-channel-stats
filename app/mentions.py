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
from urllib.parse import urlparse

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


def names_match(a: str, b: str) -> bool:
    """Whether `a` and `b` (any casing) refer to the same span of text —
    exact (word-for-word, case-insensitive) or a plausible Russian case
    variant of each other (see _names_declension_match). Used to match an
    extracted name against a link's anchor text from the same post (see
    channel_stat._extract_links and
    app.ui.compare.mentions_view._highlight_names_with_links) — NER might
    include a trailing emoji a link's anchor span doesn't, or the two might
    tag slightly different case forms of the same word, so this is a
    little more tolerant than a bare string comparison."""
    a, b = a.strip().casefold(), b.strip().casefold()
    if not a or not b:
        return False
    return _words(a) == _words(b) or _names_declension_match(a, b)


def is_telegram_link(url: str) -> bool:
    """Whether `url` is a t.me/telegram.me/telegram.org link -- the line
    every fair/fake/promo classification in this module (see
    classify_channel_links) and the Mentions view's own stats draw between
    "a real Telegram account" and "at best a web page mentioning someone"."""
    host = urlparse(url).netloc.lower()
    return host in ("t.me", "telegram.me", "telegram.org") or host.endswith(".t.me")


def tg_deep_link(url: str) -> str:
    """`url` converted to its tg:// deep-link equivalent when it's a t.me
    link this can confidently translate -- every desktop Telegram client
    registers tg:// as its own URL scheme, so opening this instead of the
    plain t.me url launches straight into it rather than a browser tab
    that then has to hand off (or doesn't). Used everywhere this app opens
    an external link (see app.ui.widgets.open_external_link) so a t.me
    link opens in Telegram app-wide, not just in one view.

    `url` unchanged for anything else: a non-Telegram link (opens in the
    browser as always), a bare private t.me/c/<id> link with no message id
    to resolve, or a join/invite link -- tg:// join links need the invite
    hash pulled out differently, and it's not worth it for how rarely one
    ever shows up here."""
    if not is_telegram_link(url):
        return url
    parts = urlparse(url).path.strip("/").split("/")
    if not parts or not parts[0]:
        return url
    if parts[0] == "c" and len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
        return f"tg://privatepost?channel={parts[1]}&post={parts[2]}"
    if parts[0] == "c":
        return url
    if parts[0].startswith(("joinchat", "+")):
        return url
    if len(parts) >= 2 and parts[1].isdigit():
        return f"tg://resolve?domain={parts[0]}&post={parts[1]}"
    return f"tg://resolve?domain={parts[0]}"


def tg_identity_key(url: str) -> str | None:
    """The stable identity a Telegram `url` resolves to -- "@username" for
    a public profile/post link, or the bare internal channel id for a
    private t.me/c/<id>/<msg> link (mentions.md has no username to file a
    private channel under, so a row for one has that raw number sitting in
    its own "id" column instead -- see resolve_telegram_link). None for a
    link this can't be reduced to an identity at all (a join/invite link,
    say) or that isn't a Telegram link in the first place."""
    if not is_telegram_link(url):
        return None
    parts = urlparse(url).path.strip("/").split("/")
    if not parts or not parts[0]:
        return None
    if parts[0] == "c" and len(parts) >= 2 and parts[1].isdigit():
        return parts[1]
    if parts[0].startswith(("joinchat", "+")):
        return None
    return f"@{parts[0]}"


def canonical_link_key(url: str) -> str:
    """Case-insensitive grouping key for `url` -- a Telegram username is
    itself case-insensitive (t.me/geekography and t.me/Geekography are the
    same channel), so two links that only differ by casing there would
    otherwise count as two distinct links throughout classify_channel_links
    and the Mentions view's own link stats (_link_balance_stats_full,
    _most_repeated_link_full). Non-Telegram links are left exactly as they
    are -- a web URL's path is generally case-sensitive, unlike a Telegram
    username."""
    return url.casefold() if is_telegram_link(url) else url


def tg_has_post_id(url: str) -> bool:
    """Whether Telegram `url` points at a specific post (a numeric second
    path segment) rather than a channel's bare root -- t.me/c/<id> always
    carries the internal channel id as its own first segment, so "a
    specific post" there needs a *third* segment instead. Used to tell a
    channel's own "subscribe to us" self-link (bare, no post) from a post
    it's genuinely referencing (see classify_channel_links' own-channel
    exclusion) -- both are otherwise the same Telegram link shape."""
    parts = urlparse(url).path.strip("/").split("/")
    if not parts or not parts[0]:
        return False
    if parts[0] == "c":
        return len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit()
    return len(parts) >= 2 and parts[1].isdigit()


def resolve_telegram_link(url: str, texts: list[str], store: MentionsStore) -> dict | None:
    """The mentions.md row (if any) already known for Telegram `url` --
    checked first by identity (tg_identity_key(url) against every row's
    own id -- the only thing that works for a private channel, whose row
    has no username to go by, just its raw internal id), then by whether
    any of `texts` (a link's own anchor text, across every post it was
    seen in) names a row MentionsStore.find_row already recognizes.

    Deliberately NOT MentionsStore.find_row_by_link (an exact-url lookup
    against a row's "unclear links" column): that column is a
    human-curated pending-review scratch list (see MentionsStore's own
    docstring), never populated by the normal Link… resolution flow in
    app.ui.compare.mentions_view (attach_name only, never attach_link), so
    in practice it almost never has anything to match against -- using it
    here is what made "Fair mentions" read as permanently 0."""
    key = tg_identity_key(url)
    if key is not None:
        needle = key.casefold()
        for row in store.rows:
            if (row.get("id") or "").strip().casefold() == needle:
                return row
    for text in texts:
        row = store.find_row(text)
        if row is not None:
            return row
    return None


def normalize_links(raw_links) -> list[dict]:
    """[{"text","url"}, …] regardless of which shape `raw_links` (a row's
    stored "links" field) is in: the current one (see
    channel_stat._extract_links) or the flat url-string list it used to be
    before anchor text was captured — a checkpoint fetched before that
    change won't have the richer shape until it's re-fetched (see
    tools.mentions_refresh), and this is what lets code that reads `links`
    not care which one it's looking at."""
    out = []
    for link in raw_links or []:
        if isinstance(link, str) and link.strip():
            out.append({"text": link, "url": link})
        elif isinstance(link, dict) and link.get("url"):
            out.append({"text": link.get("text") or link["url"], "url": link["url"]})
    return out


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

    def find_row_by_link(self, url: str) -> dict | None:
        """The row (if any) whose "unclear links" already contain `url`
        exactly. Used to color a post's link-bearing highlighted name in
        the Mentions view's texts table (see
        app.ui.compare.mentions_view._highlight_names_with_links): if the
        exact same link a name is hyperlinked to in this post is already
        sitting in some row's unclear links, that's a strong hint of which
        person this is even when the bare name text alone is ambiguous
        (e.g. a first name with no surname)."""
        url = (url or "").strip()
        if not url:
            return None
        for row in self.rows:
            if url in (row.get("links") or []):
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


# ------------------------------------------------------- link classification
# A non-Telegram link seen on this many distinct posts or more reads as the
# channel's own recurring plug (a Boosty/Patreon subscribe link, a shop...)
# rather than a one-off mention of a person -- see classify_channel_links's
# "promo" status. Deliberately low: a genuine one-off mention practically
# never repeats verbatim across posts the way a standing bio/subscribe link
# does.
_PROMO_REPEAT_THRESHOLD = 2


def classify_channel_links(entries: list[dict], store: MentionsStore,
                           name_exceptions: NameExceptions | None = None,
                           own_channel_key: str | None = None
                           ) -> dict[str, dict]:
    """Full-history classification of every link in `entries` (see
    channel_stat.py's checkpoint field `all_links` -- one {"date", "links":
    [{"text","url"}, ...]} entry per *scanned* post, not just the
    checkpoint's scored top-N `rows` pool used everywhere else in
    app.ui.compare.mentions_view), keyed by canonical_link_key(url) (case
    folded for a Telegram link, since t.me/geekography and t.me/Geekography
    are the same channel and would otherwise count as two distinct links):

        {"status": "fair" | "unresolved" | "fake" | "promo",
         "text": <a representative anchor text>, "count": <occurrences>,
         "names": <every distinct anchor text that named someone -- "fake" only>,
         "post_ids": {anchor text: [every post id it was seen under], ...},
         "url": <the exact-case url variant seen most often, for display>}

    `own_channel_key` -- this channel's own tg_identity_key(url), from
    whatever link the checkpoint itself was fetched under -- lets a bare
    self-link (this channel linking to its own root, no post: a "subscribe
    to us" plug reusing the exact same shape as crediting a person by
    linking to their channel) be excluded entirely rather than misread as
    a mention. A self-link to one of its *own* specific posts (has a post
    id -- see tg_has_post_id) is kept and classified normally; only the
    bare, post-less form is presumptively just a plug. None (the default)
    disables this check, e.g. for a caller that hasn't looked up the
    channel's own identity.

    Two of the four statuses cost nothing beyond a mentions.md lookup:

    - "fair": a Telegram link that resolves (see resolve_telegram_link) to
      a mentions.md row already -- either by identity (a private
      t.me/c/<id> link's own internal id, or a public link's username,
      matching some row's own "id") or by one of its anchor texts naming a
      row MentionsStore.find_row already recognizes.
    - "unresolved": a Telegram link *not* yet in mentions.md -- a Telegram
      anchor inherently names a real account, so this needs no further
      check either; it's exactly what becomes "fair" once linked (see the
      Mentions view's "Unresolved fair links" popup).

    A non-Telegram link is checked per distinct anchor text, cheapest
    first: find_known_names_in_text (a plain dictionary scan against
    mentions.md's own id/name candidates, no model) before falling back to
    extract_person_names (NER) only if that finds nothing -- and this runs
    for *every* anchor the url was ever seen under, not gated by how often
    the url itself repeats. That matters because a channel's own repeat
    plug link is often reused, post after post, to credit whichever person
    is actually featured that time (e.g. always boosty.to/<photographer>,
    captioned with a different model's name each post) -- gating the name
    check on repetition would read every one of those as "obviously not a
    mention" and miss every single name. So:

    - "fake": *any* of the url's anchor texts names someone (a real
      person, but only web-linked, not a Telegram identity).
    - "promo": no anchor text names anyone, but the url repeats across >=
      _PROMO_REPEAT_THRESHOLD distinct posts anyway -- a standing plug
      (Boosty/Patreon/shop) with a caption that's never actually a name
      ("Смотреть здесь", "Boosty" every time).
    - otherwise dropped from the result entirely -- not fair, fake,
      unresolved or promo, just not a mention of anyone (a one-off web
      link with some other non-name caption).

    This is still what makes classifying the *entire* scanned history
    affordable where running full extraction on every post's text
    wouldn't be: NER only ever runs on the channel's distinct link anchor
    texts -- typically a few dozen at most -- never on the hundreds or
    thousands of posts scanned to find them."""
    known_candidates = [n for row in store.rows
                        for n in ([row.get("id", "")] + list(row.get("names") or []))
                        if n.strip()]
    own_key_cf = own_channel_key.casefold() if own_channel_key else None
    agg: dict[str, dict] = {}
    for entry in entries:
        post_id = entry.get("id")
        for link in entry.get("links") or []:
            raw_url = link.get("url")
            if not raw_url:
                continue
            key = canonical_link_key(raw_url)
            a = agg.setdefault(key, {"texts": [], "post_ids": {}, "count": 0, "url_counts": {}})
            a["count"] += 1
            a["url_counts"][raw_url] = a["url_counts"].get(raw_url, 0) + 1
            text = link.get("text") or raw_url
            if text not in a["texts"]:
                a["texts"].append(text)
            # Every post seen under this exact anchor text (not just the
            # first) -- the Mentions view's Link report jumps straight to
            # the one post if there's only one, or offers a pick-list if
            # this exact name/link combination came from several (see
            # mentions_view._open_link_report/_open_link_report_sources).
            post_id_list = a["post_ids"].setdefault(text, [])
            if post_id not in post_id_list:
                post_id_list.append(post_id)

    out: dict[str, dict] = {}
    for key, agg_entry in agg.items():
        texts, count = agg_entry["texts"], agg_entry["count"]
        post_ids = agg_entry["post_ids"]
        # Whichever exact casing showed up most often is what gets shown --
        # ties broken by first-seen (dict insertion order).
        url = max(agg_entry["url_counts"].items(), key=lambda kv: kv[1])[0]
        rep_text = texts[0]
        if is_telegram_link(url):
            if (own_key_cf is not None and not tg_has_post_id(url)
                    and (tg_identity_key(url) or "").casefold() == own_key_cf):
                continue  # this channel linking to its own bare root -- a
                          # subscribe plug, not a mention of anyone
            row = resolve_telegram_link(url, texts, store)
            out[key] = {"status": "fair" if row is not None else "unresolved",
                       "text": rep_text, "count": count, "post_ids": post_ids, "url": url}
            continue
        # Checked per distinct anchor text, not gated by `count` up front --
        # a channel's own repeat plug link is often *also* reused, post
        # after post, to credit whichever person is actually featured that
        # time (e.g. always boosty.to/<photographer>, captioned with a
        # different model's name each post), so a high repeat count alone
        # can't rule out a real name; only the absence of one across every
        # anchor this url was ever seen under can. Every anchor that names
        # someone is kept (not just the first) -- one repeat plug link
        # credits as many different people as it was ever captioned with,
        # and "Fake mentions" counts each of those, not the one link.
        name_texts: list[str] = []
        for text in texts:
            names = find_known_names_in_text(text, known_candidates)
            if not names:
                names = extract_person_names(text)
                if name_exceptions is not None:
                    names = name_exceptions.filter(names)
            if names:
                name_texts.append(text)
        if name_texts:
            out[key] = {"status": "fake", "text": name_texts[0], "names": name_texts,
                       "count": count, "post_ids": post_ids, "url": url}
        elif count >= _PROMO_REPEAT_THRESHOLD:
            out[key] = {"status": "promo", "text": rep_text, "count": count,
                       "post_ids": post_ids, "url": url}
        # else: no name found under any anchor, and not repeated enough to
        # read as a standing plug either -- dropped, not a mention.
    return out
