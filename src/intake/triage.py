"""Posting triage: refine category and tag audiences.

Separates events (info sessions, insight days, recruiting summits) from
work postings, and tags who a posting targets: underclassmen, diversity
cohorts. Events matter: early pipeline participation and demonstrated
interest feed the hiring funnel before applications open.
"""

from __future__ import annotations

import re

EVENT_TITLE_RE = re.compile(
    r"\bevents?\b|open house|info(rmation)? session|insight (day|week|series|program)"
    r"|summit|conference|meetup|career fair|discovery day|early insight"
    r"|virtual (event|session)|recruiting event|tech talk|office hours|webinar"
    r"|hackathon|game jam",
    re.IGNORECASE,
)
EVENT_URL_RE = re.compile(
    r"eightfold\.ai/events|splashthat\.com|eventbrite\.|lu\.ma/|/events?/"
    r"|course-offering",
    re.IGNORECASE,
)

DIVERSITY_RE = re.compile(
    r"\bwomen\b|\bgirls?\b|black|african[- ]american|hispanic|latin[oax]"
    r"|indigenous|native american|first[- ]gen|lgbtq|\bpride\b|veteran"
    r"|disabilit|underrepresented|minorit|diversity|\bhbcu\b|\bswe\b society",
    re.IGNORECASE,
)
UNDERCLASSMEN_RE = re.compile(
    r"freshm[ae]n|sophomore|first[- ]year|second[- ]year|underclassmen"
    r"|early (career|talent|insight|id)\b|class of 20(28|29|30)",
    re.IGNORECASE,
)


def refine_category(category: str, title: str, url: str) -> str:
    """Promote to 'event' on clear signals. Scholarship/research stand."""
    if category in ("scholarship", "research"):
        return category
    if EVENT_TITLE_RE.search(title) or EVENT_URL_RE.search(url):
        return "event"
    return category


def audience_tags(*texts: str) -> list[str]:
    blob = " ".join(t for t in texts if t)
    tags = []
    if UNDERCLASSMEN_RE.search(blob):
        tags.append("underclassmen")
    if DIVERSITY_RE.search(blob):
        tags.append("diversity")
    return tags
