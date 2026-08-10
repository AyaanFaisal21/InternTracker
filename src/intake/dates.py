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


def parse_season(title: str) -> str | None:
    """"Fall 2026" / "Summer 2027" from a title; bare year -> "2026";
    None when the title carries neither (Apple's year-round umbrellas)."""
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
