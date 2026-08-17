"""Posting-date parsing from source payloads.

Every parser returns None on anything it cannot read with confidence.
A missing date is filled later by the verifier (LLM fallback), never
guessed here.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

RELATIVE_RE = re.compile(r"posted\s+(today|yesterday|(\d+)\+?\s+days?\s+ago)", re.IGNORECASE)


def parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_epoch(value) -> datetime | None:
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    if ts > 1e12:  # epoch millis
        ts /= 1000.0
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def parse_workday_relative(text: str | None) -> datetime | None:
    """"Posted 3 Days Ago" -> approximate date. "Posted 30+ Days Ago" is
    unbounded, so it returns None rather than a wrong number."""
    if not text:
        return None
    m = RELATIVE_RE.search(text)
    if not m:
        return None
    if "+" in m.group(0):
        return None
    now = datetime.now(timezone.utc)
    word = m.group(1).lower()
    if word == "today":
        return now
    if word == "yesterday":
        return now - timedelta(days=1)
    return now - timedelta(days=int(m.group(2)))


SEASON_RE = re.compile(r"\b(fall|spring|summer|winter)\b[\s,'-]*((?:20)?\d{2})?", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(20\d{2})\b")

# A joined season list ("Summer/Fall 2027", "Fall or Winter") or an
# open-start phrase means several possible starts. Such postings carry
# season None; the unknown-season rule then passes every season filter.
# A single label would wrongly hide them from the other seasons' filters.
_SEASON_WORD = r"(?:fall|autumn|spring|summer|winter)"
_SEASON_YEAR = r"(?:\s*(?:20\d{2}|'\d{2}))?"
FLEXIBLE_SEASON_RE = re.compile(
    rf"{_SEASON_WORD}{_SEASON_YEAR}\s*(?:[/,&+-]|\bor\b|\band\b)\s*"
    rf"(?:(?:or|and)\b\s*)?{_SEASON_WORD}"
    r"|flexible start|year[- ]?round|ongoing basis|multiple start dates"
    r"|all seasons",
    re.IGNORECASE,
)


def season_is_flexible(text: str) -> bool:
    """True when the text offers several starts or an open one."""
    return bool(FLEXIBLE_SEASON_RE.search(text))


def _default_current_year() -> int:
    """Clock seam: tests monkeypatch this to pin the recruiting window."""
    return datetime.now(timezone.utc).year


def season_year_plausible(year: int, current_year: int | None = None) -> bool:
    """Whether a year can belong to a live recruiting cycle. Page text is
    full of copyright lines and founding years ("© 2000 Keysight"); only
    [current_year - 1, current_year + 3] can be a real season."""
    cy = current_year if current_year is not None else _default_current_year()
    return cy - 1 <= year <= cy + 3


def parse_season(title: str, current_year: int | None = None) -> str | None:
    """"Fall 2026" / "Summer 2027" from a title; bare year -> "2026";
    None when the title carries neither (Apple's year-round umbrellas)
    or names several seasons (a flexible posting must not get one).

    A year outside the plausible recruiting window parses to None — a
    "Summer 2020" or a bare "2006" is page chrome (copyright, founding
    year), never a cycle, and must not override anything. current_year
    pins the window for tests; the default reads the clock at call time.
    """
    if season_is_flexible(title):
        return None
    m = SEASON_RE.search(title)
    if m:
        term = m.group(1).capitalize()
        year = m.group(2)
        if year and len(year) == 2:
            year = "20" + year
        if not year:
            ym = YEAR_RE.search(title)
            year = ym.group(1) if ym else None
        if year and not season_year_plausible(int(year), current_year):
            return None
        return f"{term} {year}" if year else term
    ym = YEAR_RE.search(title)
    if ym and not season_year_plausible(int(ym.group(1)), current_year):
        return None
    return ym.group(1) if ym else None


def resolve_season(
    title: str, page_text: str = "", current_year: int | None = None
) -> str | None:
    """Season for the rule gate: title first, page text fallback.

    A flexible source stops the chain instead of falling through, so a
    "Summer/Fall" title cannot pick a single season out of the page text.
    A single-season title stays decisive over flexible page text."""
    if season_is_flexible(title):
        return None
    season = parse_season(title, current_year)
    if season:
        return season
    if season_is_flexible(page_text):
        return None
    return parse_season(page_text, current_year)


def reconcile_season(
    stored: str | None,
    title: str,
    page_text: str = "",
    current_year: int | None = None,
) -> str | None:
    """Final season given a source-stored value plus the resolve chain.

    The gate and the offline reclassify pass share this. A stored season
    normally stands (list sources state cycles pages omit), with three
    exceptions:
      - stored empty: fill from the chain (title, then page text).
      - stored year implausible: a page-chrome artifact; recompute from
        the chain, else None.
      - stored bare ("Summer") while the title carries a year: the title
        wins ("Summer 2026"); a bare-year title joins the stored word
        ("Summer" + "2026" -> "Summer 2026").
    Flexible-season semantics hold: a flexible title parses to None, so
    it never overrides a stored bare season, and a stored None stays None.
    """
    resolved = resolve_season(title, page_text, current_year)
    if not stored:
        return resolved
    m = YEAR_RE.search(stored)
    if m:
        if season_year_plausible(int(m.group(1)), current_year):
            return stored
        return resolved  # stale stored year: recompute or clear
    title_season = parse_season(title, current_year)
    if title_season and (ty := YEAR_RE.search(title_season)):
        if title_season[0].isalpha():
            return title_season
        return f"{stored} {ty.group(1)}"
    return stored
