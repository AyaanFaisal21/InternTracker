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


def parse_season(title: str) -> str | None:
    """"Fall 2026" / "Summer 2027" from a title; bare year -> "2026";
    None when the title carries neither (Apple's year-round umbrellas)
    or names several seasons (a flexible posting must not get one)."""
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
        return f"{term} {year}" if year else term
    ym = YEAR_RE.search(title)
    return ym.group(1) if ym else None


def resolve_season(title: str, page_text: str = "") -> str | None:
    """Season for the rule gate: title first, page text fallback.

    A flexible source stops the chain instead of falling through, so a
    "Summer/Fall" title cannot pick a single season out of the page text.
    A single-season title stays decisive over flexible page text."""
    if season_is_flexible(title):
        return None
    season = parse_season(title)
    if season:
        return season
    if season_is_flexible(page_text):
        return None
    return parse_season(page_text)
