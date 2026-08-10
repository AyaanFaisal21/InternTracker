"""Role-type classification from titles. Tag taxonomy for the dashboard."""

from __future__ import annotations

import re

# Ordered: first match wins. Specific families before the generic SWE catchall.
ROLE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("PM/TPM", re.compile(
        r"product manage|program manage|technical program|\btpm\b|product intern", re.I)),
    ("FDE/Solutions", re.compile(
        r"forward[- ]deployed|solutions? engineer|deployment strategist|sales engineer"
        r"|application engineer", re.I)),
    ("Data/ML", re.compile(
        r"machine learning|\bml\b|\bai\b|deep learning|\bllm\b|data scien|data engineer"
        r"|data analy|computer vision|\bnlp\b|research", re.I)),
    ("Hardware", re.compile(
        r"hardware|asic|fpga|silicon|circuit|electrical|mechanical|firmware"
        r"|system design|validation", re.I)),
    ("Quant", re.compile(r"quant|trading|trader", re.I)),
    ("SWE", re.compile(
        r"software|swe\b|developer|full[- ]?stack|backend|frontend|infra|platform"
        r"|mobile|ios|android|security|compiler|gpu|cuda|graphics|robotics|engineer", re.I)),
]


def classify_role(title: str) -> str:
    for tag, pat in ROLE_PATTERNS:
        if pat.search(title):
            return tag
    return "Other"
