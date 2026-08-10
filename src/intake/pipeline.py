"""Pipeline orchestrator.

Flow per cycle:
  1. Poll all detectors.
  2. Upsert detections into the store (dedupe happens here).
  3. New postings run the rule gate. Fail -> REJECTED. Pass -> GATED.
  4. GATED postings go to the verifier agent. Verdict sets VERIFIED or REJECTED.
  5. VERIFIED postings are handed to the publisher hook.

No posting reaches the publisher without a stored verdict.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

import httpx

from .config import Settings
from .dates import parse_iso, parse_season
from .detectors import (
    BROWSER_DETECTORS,
    CUSTOM_DETECTORS,
    AshbyDetector,
    Detector,
    GithubListDetector,
    GreenhouseDetector,
    LeverDetector,
    WorkdayDetector,
)
from .schema import Posting, Status
from .store import Store
from .verify import VerifierAgent, run_rules

log = logging.getLogger("intake")


@dataclass
class CycleReport:
    detected: int = 0
    new: int = 0
    rule_rejected: int = 0
    agent_rejected: int = 0
    verified: int = 0
    published: int = 0
    errors: list[str] = field(default_factory=list)


def default_publisher(p: Posting) -> None:
    """Placeholder publish hook. Replace with the web-app API call."""
    log.info("PUBLISH %s — %s (%s)", p.company, p.title, p.url)


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        store: Store | None = None,
        detectors: list[Detector] | None = None,
        verifier: VerifierAgent | None = None,
        publisher: Callable[[Posting], None] = default_publisher,
    ):
        self.settings = settings
        self.store = store or Store(settings.db_path)
        wl = settings.watchlist
        self.detectors: list[Detector] = detectors or [
            GreenhouseDetector(wl.greenhouse),
            LeverDetector(wl.lever),
            AshbyDetector(wl.ashby),
            WorkdayDetector(wl.workday),
            *[CUSTOM_DETECTORS[name]() for name in wl.custom if name in CUSTOM_DETECTORS],
            *[BROWSER_DETECTORS[name]() for name in wl.browser if name in BROWSER_DETECTORS],
            GithubListDetector(wl.github_lists, max_age_days=settings.list_max_age_days),
        ]
        self.verifier = verifier or VerifierAgent(settings)
        self.publisher = publisher
        self.http = httpx.Client(
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )

    def run_cycle(self, verify: bool = True) -> CycleReport:
        """One full pass. verify=False stops after the rule gate — postings
        park at GATED. Use it to inspect acquisition without spending on the
        verifier (no API key required)."""
        report = CycleReport()

        # 1-2: detect and dedupe
        for det in self.detectors:
            try:
                detections = det.poll()
            except Exception as e:  # a detector crash must not kill the cycle
                report.errors.append(f"{det.name}: {e}")
                continue
            report.detected += len(detections)
            for d in detections:
                _, is_new = self.store.upsert_detection(d)
                report.new += int(is_new)

        # 3: rule gate + canonical URL resolution
        for p in self.store.by_status(Status.PENDING):
            reason, canonical, degrees = run_rules(p, self.http)
            if reason:
                p.status, p.reject_reason = Status.REJECTED, reason
                report.rule_rejected += 1
            else:
                p.canonical_url = canonical
                p.degree_levels = degrees
                p.season = parse_season(p.title)
                p.status = Status.GATED
            self.store.update(p)

        if not verify:
            return report

        # 4: agent verification
        for p in self.store.by_status(Status.GATED):
            try:
                verdict = self.verifier.verify(p)
            except Exception as e:
                report.errors.append(f"verify {p.id}: {e}")
                continue  # stays GATED, retried next cycle
            p.verdict = verdict
            if p.date_posted is None and verdict.date_posted:
                p.date_posted = parse_iso(verdict.date_posted)
            if verdict.season:
                p.season = verdict.season
            if verdict.approved:
                p.status = Status.VERIFIED
                report.verified += 1
            else:
                p.status = Status.REJECTED
                p.reject_reason = "; ".join(verdict.reasons)[:500]
                report.agent_rejected += 1
            self.store.update(p)

        # 5: publish
        for p in self.store.by_status(Status.VERIFIED):
            try:
                self.publisher(p)
            except Exception as e:
                report.errors.append(f"publish {p.id}: {e}")
                continue
            p.status = Status.PUBLISHED
            self.store.update(p)
            report.published += 1

        return report
