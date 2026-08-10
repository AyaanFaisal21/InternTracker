"""Rule gate: deterministic checks that run before the agent.

Purpose: reject the obvious junk cheaply so the agent only reasons about
ambiguous cases. A rule rejection is final. A rule pass is not approval —
it only forwards the posting to the agent.

Each rule returns None on pass, or a short reject reason on fail.
"""

from __future__ import annotations

import re

import httpx

from ..degrees import classify, extract_qualifications, strip_tags, workday_detail_text
from ..normalize import resolve_canonical, strip_tracking
from ..schema import DegreeLevel, Posting, Source

DISQUALIFYING_RE = re.compile(
    r"unpaid|ambassador|brand rep|commission[- ]only|volunteer", re.IGNORECASE
)
NON_TECH_RE = re.compile(
    r"\b(sales|marketing|recruit(er|ing)|hr\b|legal|finance intern|accounting)\b",
    re.IGNORECASE,
)
# Tech signal broad enough to rescue hybrid titles ("AI Innovation Intern -
# Service Sales"). Ambiguous titles go to the verifier, not the reject pile.
TECH_SIGNAL_RE = re.compile(
    r"software|engineer|developer|\bai\b|\bml\b|machine learning|data"
    r"|technical|technology|product manage|program manage|forward[- ]deployed"
    r"|solutions? engineer|deployment strategist",
    re.IGNORECASE,
)


def check_title(p: Posting) -> str | None:
    """Hard-reject only unambiguous cases. The verifier owns the gray zone."""
    if DISQUALIFYING_RE.search(p.title):
        return "disqualifying term in title"
    if NON_TECH_RE.search(p.title) and not TECH_SIGNAL_RE.search(p.title):
        return "non-tech role"
    return None


def run_rules(
    p: Posting, client: httpx.Client
) -> tuple[str | None, str | None, list[DegreeLevel], str | None]:
    """Run rules, resolve the canonical URL, classify degrees, pull a
    qualifications excerpt.

    Returns (reject_reason, canonical_url, degree_levels, qualifications).
    On rejection the last three are empty.
    """
    reason = check_title(p)
    if reason:
        return reason, None, [], None
    reason, canonical, page_text = resolve_canonical(p.url, client)
    if reason and ("403" in reason or "429" in reason):
        # WAF block, not a dead posting (Tesla et al. reject bot clients).
        # Keep the source URL; the verifier or a browser pass judges later.
        canonical, page_text, reason = strip_tracking(p.url), "", None
    if reason:
        return reason, None, [], None
    if Source.WORKDAY in p.sources:
        page_text = workday_detail_text(canonical or p.url, client) or page_text
    text = strip_tags(page_text)
    return None, canonical, classify(p.title, text), extract_qualifications(text)
