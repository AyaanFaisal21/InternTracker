"""Heuristic degree-level classification. No LLM.

First pass over title + page text. The verifier's verdict, when present,
overrides this. Empty result means "no requirement found" — the frontend
treats that as open to all levels.
"""

from __future__ import annotations

import re

import httpx

from .schema import DegreeLevel

PATTERNS: dict[DegreeLevel, re.Pattern] = {
    "BS": re.compile(r"bachelor|\bb\.?s\.?\b|\bbsc\b|undergrad(?:uate)?", re.IGNORECASE),
    "MS": re.compile(r"master|\bm\.?s\.?\b|\bmsc\b|\bmeng\b", re.IGNORECASE),
    "PhD": re.compile(r"ph\.?\s?d|doctoral|doctorate", re.IGNORECASE),
}

TAG_RE = re.compile(r"<[^>]+>")
WORKDAY_URL_RE = re.compile(r"https://([^/]+)/en-US/([^/]+)(/job/.+)$")


def strip_tags(html: str) -> str:
    return TAG_RE.sub(" ", html)


def classify(title: str, page_text: str) -> list[DegreeLevel]:
    """Degree levels the posting is open to, best-effort.

    A degree named in the title is decisive ("PhD Research Intern" -> PhD
    only). Otherwise scan the page text for every level mentioned.
    """
    in_title = [lvl for lvl, pat in PATTERNS.items() if pat.search(title)]
    if in_title:
        return in_title
    return [lvl for lvl, pat in PATTERNS.items() if pat.search(page_text)]


def workday_detail_text(url: str, client: httpx.Client) -> str:
    """Workday job pages are JS shells; the description lives on the CXS
    detail endpoint. Derive it from the public URL. Empty string on any
    failure — the heuristic then falls back to title-only."""
    m = WORKDAY_URL_RE.match(url)
    if not m:
        return ""
    host, site, path = m.groups()
    tenant = host.split(".")[0]
    try:
        resp = client.get(f"https://{host}/wday/cxs/{tenant}/{site}{path}", timeout=15.0)
        resp.raise_for_status()
        desc = resp.json().get("jobPostingInfo", {}).get("jobDescription", "")
    except (httpx.HTTPError, ValueError):
        return ""
    return strip_tags(desc)
