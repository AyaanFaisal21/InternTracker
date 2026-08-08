"""Ashby detector. Polls the public job-board API — no auth required.

Endpoint: GET https://api.ashbyhq.com/posting-api/job-board/{name}
"""

from __future__ import annotations

import httpx

from ..schema import RawDetection, Source
from .base import looks_like_swe_internship

API = "https://api.ashbyhq.com/posting-api/job-board/{name}"


class AshbyDetector:
    name = "ashby"

    def __init__(self, board_names: list[str], client: httpx.Client | None = None):
        self.board_names = board_names
        self.client = client or httpx.Client(timeout=15.0)

    def poll(self) -> list[RawDetection]:
        out: list[RawDetection] = []
        for name in self.board_names:
            try:
                resp = self.client.get(API.format(name=name))
                resp.raise_for_status()
            except httpx.HTTPError:
                continue
            for job in resp.json().get("jobs", []):
                title = job.get("title", "")
                if not looks_like_swe_internship(title):
                    continue
                out.append(
                    RawDetection(
                        source=Source.ASHBY,
                        company=name,
                        title=title,
                        url=job.get("jobUrl", "") or job.get("applyUrl", ""),
                        locations=[job.get("location", "")],
                        payload={"ashby_listed": job.get("isListed", True)},
                    )
                )
        return out
