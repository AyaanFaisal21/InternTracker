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
