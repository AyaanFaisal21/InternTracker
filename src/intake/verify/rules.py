"""Rule gate: deterministic checks that run before the agent.

Purpose: reject the obvious junk cheaply so the agent only reasons about
ambiguous cases. A rule rejection is final. A rule pass is not approval —
it only forwards the posting to the agent.

Each rule returns None on pass, or a short reject reason on fail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

from ..dates import parse_season
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


@dataclass
class GateResult:
    reject_reason: str | None = None
    canonical_url: str | None = None
    degree_levels: list[DegreeLevel] = field(default_factory=list)
    qualifications: str | None = None
    season: str | None = None


def run_rules(p: Posting, client: httpx.Client) -> GateResult:
    """Run rules, resolve the canonical URL, classify degrees and season,
    pull a qualifications excerpt. Season falls back to page text because
    many titles omit the cycle the page states."""
    reason = check_title(p)
    if reason:
        return GateResult(reject_reason=reason)
    reason, canonical, page_text = resolve_canonical(p.url, client)
    if reason and ("403" in reason or "429" in reason or "unreachable" in reason):
        # WAF block or transient network failure, not a dead posting (Tesla
        # 403s bot clients; Roblox timed out mid-sweep while fully live).
        # Keep the source URL; the verifier or a later pass judges.
        canonical, page_text, reason = strip_tracking(p.url), "", None
    if reason:
        return GateResult(reject_reason=reason)
    if Source.WORKDAY in p.sources:
        page_text = workday_detail_text(canonical or p.url, client) or page_text
    text = strip_tags(page_text)
    return GateResult(
        canonical_url=canonical,
        degree_levels=classify(p.title, text),
        qualifications=extract_qualifications(text),
        season=parse_season(p.title) or parse_season(text[:4000]),
    )
