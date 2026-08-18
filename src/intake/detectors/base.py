"""Detector interface.

Each detector polls one source and returns RawDetections. Detectors do not
filter, dedupe, or verify — that is pipeline work. A detector's only job is
completeness: report everything it sees that could be a SWE internship.
"""

from __future__ import annotations

import re
from typing import Protocol

from ..schema import RawDetection

# Coarse pre-filter. Applied at the detector level only to cut noise from
# full-board polls (an ATS board lists every role at the company).
INTERN_RE = re.compile(
    r"\bintern(ship)?s?\b|\bco[- ]?op\b|student researcher", re.IGNORECASE
)
SWE_RE = re.compile(
    r"software|swe\b|engineer|developer|infra|backend|frontend|full[- ]?stack"
    r"|machine learning|\bml\b|\bai\b|data|platform|\bsystems?\b|security|mobile|ios|android"
    # research-heavy employers (NVIDIA et al.) title CS intern roles like this:
    r"|deep learning|\bllm\b|gpu|cuda|compiler|graphics|robotics|computer vision"
    r"|perception|autonomous|firmware|research|programmer",
    re.IGNORECASE,
)


# Slugs whose title-cased form is wrong. Only entries we actually watch.
_DISPLAY_OVERRIDES = {"openai": "OpenAI", "ibm": "IBM", "nvidia": "NVIDIA"}


def company_from_slug(slug: str) -> str:
    """Display name for an ATS slug ("dedalus-labs" -> "Dedalus Labs").

    Boards that expose a real company name should use it; this is for the
    ones that do not. A raw slug shows the employer lowercased and hyphenated
    beside properly cased names from other sources, and because company is
    half the dedupe key, the two spellings never merge into one posting.
    """
    if slug.lower() in _DISPLAY_OVERRIDES:
        return _DISPLAY_OVERRIDES[slug.lower()]
    return " ".join(w.capitalize() for w in slug.replace("_", "-").split("-") if w) or slug


def looks_like_swe_internship(title: str) -> bool:
    return bool(INTERN_RE.search(title)) and bool(SWE_RE.search(title))


class Detector(Protocol):
    name: str

    def poll(self) -> list[RawDetection]:
        """Return all current candidate postings from this source."""
        ...
