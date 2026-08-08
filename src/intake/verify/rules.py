"""Rule gate: deterministic checks that run before the agent.

Purpose: reject the obvious junk cheaply so the agent only reasons about
ambiguous cases. A rule rejection is final. A rule pass is not approval —
it only forwards the posting to the agent.

Each rule returns None on pass, or a short reject reason on fail.
"""

from __future__ import annotations

import re

import httpx

from ..normalize import resolve_canonical
from ..schema import Posting

DISQUALIFYING_RE = re.compile(
    r"unpaid|ambassador|brand rep|commission[- ]only|volunteer", re.IGNORECASE
)
NON_SWE_RE = re.compile(
    r"\b(sales|marketing|recruit(er|ing)|hr\b|legal|finance intern|accounting)\b",
    re.IGNORECASE,
)


def check_title(p: Posting) -> str | None:
    if DISQUALIFYING_RE.search(p.title):
        return "disqualifying term in title"
    if NON_SWE_RE.search(p.title):
        return "non-SWE role"
    return None


def run_rules(p: Posting, client: httpx.Client) -> tuple[str | None, str | None]:
    """Run all rules and resolve the canonical URL.

    Returns (reject_reason, canonical_url). On rejection the canonical URL
    is None. On pass the canonical URL is the resolved employer page.
    """
    reason = check_title(p)
    if reason:
        return reason, None
    return resolve_canonical(p.url, client)
