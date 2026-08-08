"""Greenhouse detector. Polls the public board API — no auth required.

Endpoint: GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs
Latency: postings appear here the moment the board publishes them.
"""

from __future__ import annotations

import httpx

from ..schema import RawDetection, Source
from .base import looks_like_swe_internship

API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


class GreenhouseDetector:
    name = "greenhouse"

    def __init__(self, board_tokens: list[str], client: httpx.Client | None = None):
        self.board_tokens = board_tokens
        self.client = client or httpx.Client(timeout=15.0)

    def poll(self) -> list[RawDetection]:
        out: list[RawDetection] = []
        for token in self.board_tokens:
            try:
                resp = self.client.get(API.format(token=token))
                resp.raise_for_status()
            except httpx.HTTPError:
                continue  # one bad board must not stop the sweep
            for job in resp.json().get("jobs", []):
                title = job.get("title", "")
                if not looks_like_swe_internship(title):
                    continue
                out.append(
                    RawDetection(
                        source=Source.GREENHOUSE,
                        company=token,
                        title=title,
                        url=job.get("absolute_url", ""),
                        locations=[job.get("location", {}).get("name", "")],
                        payload={"gh_job_id": job.get("id")},
                    )
                )
        return out
