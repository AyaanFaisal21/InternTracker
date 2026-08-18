"""Greenhouse detector. Polls the public board API — no auth required.

Endpoint: GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs
Latency: postings appear here the moment the board publishes them.
"""

from __future__ import annotations

import httpx

from ..dates import parse_iso
from ..schema import RawDetection, Source
from .base import looks_like_swe_internship

API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
BOARD = "https://boards-api.greenhouse.io/v1/boards/{token}"


class GreenhouseDetector:
    name = "greenhouse"

    def __init__(self, board_tokens: list[str], client: httpx.Client | None = None):
        self.board_tokens = board_tokens
        self.client = client or httpx.Client(timeout=15.0)
        self._names: dict[str, str] = {}

    def company_name(self, token: str) -> str:
        """Display name for a board token.

        The token is a slug ("datadog", "epicgames"), and using it raw shows
        the employer lowercased and unspaced next to properly cased names
        from other sources. The board endpoint carries the real name. Cached
        for the life of the process: it is not worth a request per cycle.
        """
        if token not in self._names:
            name = token
            try:
                resp = self.client.get(BOARD.format(token=token))
                resp.raise_for_status()
                name = (resp.json().get("name") or "").strip() or token
            except (httpx.HTTPError, ValueError):
                pass  # a missing name must never cost us the board's postings
            self._names[token] = name
        return self._names[token]

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
                        company=self.company_name(token),
                        title=title,
                        url=job.get("absolute_url", ""),
                        locations=[job.get("location", {}).get("name", "")],
                        date_posted=parse_iso(
                            job.get("first_published") or job.get("updated_at")
                        ),
                        payload={"gh_job_id": job.get("id")},
                    )
                )
        return out
