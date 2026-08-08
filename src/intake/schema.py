"""Data models for the intake pipeline.

RawDetection: one sighting of a posting by one detector.
Posting: the deduplicated record that moves through verification.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from enum import Enum

from typing import Literal

from pydantic import BaseModel, Field

DegreeLevel = Literal["BS", "MS", "PhD"]


class Source(str, Enum):
    GITHUB_LIST = "github_list"
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"


class Status(str, Enum):
    PENDING = "pending"        # detected, not yet gated
    GATED = "gated"            # passed rule checks, awaits agent verdict
    VERIFIED = "verified"      # agent approved, safe to publish
    REJECTED = "rejected"      # failed rules or agent verdict
    PUBLISHED = "published"    # pushed to the web app


def norm_text(s: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation. Used for dedupe keys."""
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


class RawDetection(BaseModel):
    source: Source
    company: str
    title: str
    url: str
    locations: list[str] = Field(default_factory=list)
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict = Field(default_factory=dict)  # raw source data, kept for the verifier

    def dedupe_key(self) -> str:
        """Cross-source identity: same company + same title = same posting.

        Limitation: two distinct openings with identical titles at one company
        collapse into one record. Acceptable for v1; revisit with location salt
        if it produces false merges.
        """
        raw = f"{norm_text(self.company)}|{norm_text(self.title)}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]


class Verdict(BaseModel):
    """Structured output schema for the verifier agent."""
    is_swe_internship: bool
    is_open: bool                     # application appears open, not closed/filled
    is_legitimate: bool               # real employer posting, not spam/ambassador/unpaid-scheme
    season: str | None = None         # e.g. "Summer 2026"
    degree_levels: list[DegreeLevel] = Field(default_factory=list)
    # Degree levels the posting is open to, from the page text. A posting
    # requiring "current PhD students" is ["PhD"]. Unstated -> all three.
    confidence: str                   # "high" | "medium" | "low"
    reasons: list[str]

    @property
    def approved(self) -> bool:
        return self.is_swe_internship and self.is_open and self.is_legitimate


class Posting(BaseModel):
    id: str
    company: str
    title: str
    url: str                          # as detected
    canonical_url: str | None = None  # resolved employer page; publish this, never url
    locations: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    first_seen: datetime
    status: Status = Status.PENDING
    reject_reason: str | None = None
    verdict: Verdict | None = None

    @classmethod
    def from_detection(cls, d: RawDetection) -> "Posting":
        return cls(
            id=d.dedupe_key(),
            company=d.company,
            title=d.title,
            url=d.url,
            locations=d.locations,
            sources=[d.source],
            first_seen=d.detected_at,
        )
