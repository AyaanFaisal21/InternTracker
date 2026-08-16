"""GitHub list detector. Backstop source — wide coverage, higher latency.

Instead of scraping README tables, this pulls the machine-readable
listings.json that SimplifyJobs-style repos maintain. Each entry carries a
stable id, active flag, and update timestamp, so diffing is exact.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from ..dates import parse_epoch
from ..schema import RawDetection, Source
from .base import looks_like_swe_internship


class GithubListDetector:
    name = "github_list"

    def __init__(
        self,
        listing_urls: list[str],
        max_age_days: int = 14,
        client: httpx.Client | None = None,
    ):
        """max_age_days=0 disables the age cutoff. Production runs with 0
        (Settings.list_max_age_days): an active posting is applyable at any
        age, and lists backfill old dates. A nonzero bound is the lever for
        capping first-run cost, when thousands of old entries would hit the
        rule gate with URL resolutions at once."""
        self.listing_urls = listing_urls
        self.max_age_days = max_age_days
        self.client = client or httpx.Client(timeout=30.0, follow_redirects=True)

    def poll(self) -> list[RawDetection]:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=self.max_age_days)
            if self.max_age_days
            else None
        )
        out: list[RawDetection] = []
        for url in self.listing_urls:
            try:
                resp = self.client.get(url)
                resp.raise_for_status()
                entries = resp.json()
            except (httpx.HTTPError, ValueError):
                continue
            for e in entries:
                if not e.get("active", False) or not e.get("is_visible", True):
                    continue
                title = e.get("title", "")
                if not looks_like_swe_internship(title + " intern"):  # list is intern-only
                    continue
                ts = e.get("date_posted") or e.get("date_updated") or 0
                if cutoff and ts and datetime.fromtimestamp(ts, tz=timezone.utc) < cutoff:
                    continue
                posted = parse_epoch(ts)
                out.append(
                    RawDetection(
                        source=Source.GITHUB_LIST,
                        company=e.get("company_name", ""),
                        title=title,
                        url=e.get("url", ""),
                        date_posted=posted,
                        locations=e.get("locations", []),
                        detected_at=datetime.fromtimestamp(ts, tz=timezone.utc)
                        if ts
                        else datetime.now(timezone.utc),
                        payload={"list_id": e.get("id"), "sponsorship": e.get("sponsorship")},
                    )
                )
        return out


class OpportunityListDetector:
    """Curated non-internship lists (underclassmen-opportunities schema).

    Same listings.json shape as the internship lists plus category,
    opportunity_type, target_year, and a season string. Curated for the
    audience already, so no SWE title prefilter. Inactive entries skipped.
    """

    name = "opportunity_list"

    def __init__(self, listing_urls: list[str], client: httpx.Client | None = None):
        self.listing_urls = listing_urls
        self.client = client or httpx.Client(timeout=30.0, follow_redirects=True)

    def poll(self) -> list[RawDetection]:
        from ..dates import parse_season

        out: list[RawDetection] = []
        for url in self.listing_urls:
            try:
                resp = self.client.get(url)
                resp.raise_for_status()
                entries = resp.json()
            except (httpx.HTTPError, ValueError):
                continue
            for e in entries:
                if not e.get("active", False) or not e.get("is_visible", True):
                    continue
                ts = e.get("date_posted") or e.get("date_updated") or 0
                out.append(
                    RawDetection(
                        source=Source.OPPORTUNITY_LIST,
                        company=e.get("company_name", ""),
                        title=e.get("title", ""),
                        url=e.get("url", ""),
                        locations=e.get("locations", []),
                        category=(e.get("category") or "program").lower(),
                        audience=(
                            ["underclassmen"]
                            if any("fresh" in str(y).lower() or "soph" in str(y).lower()
                                   for y in e.get("target_year") or [])
                            else []
                        ),
                        season=parse_season(e.get("season") or ""),
                        date_posted=datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None,
                        payload={
                            "list_id": e.get("id"),
                            "opportunity_type": e.get("opportunity_type"),
                            "target_year": e.get("target_year"),
                        },
                    )
                )
        return out
