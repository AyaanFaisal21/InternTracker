"""Lever detector. Polls the public postings API — no auth required.

Endpoint: GET https://api.lever.co/v0/postings/{slug}?mode=json
"""

from __future__ import annotations

import httpx

from ..dates import parse_epoch
from ..schema import RawDetection, Source
from .base import company_from_slug, looks_like_swe_internship

API = "https://api.lever.co/v0/postings/{slug}?mode=json"


class LeverDetector:
    name = "lever"

    def __init__(self, slugs: list[str], client: httpx.Client | None = None):
        self.slugs = slugs
        self.client = client or httpx.Client(timeout=15.0)

    def poll(self) -> list[RawDetection]:
        out: list[RawDetection] = []
        for slug in self.slugs:
            try:
                resp = self.client.get(API.format(slug=slug))
                resp.raise_for_status()
            except httpx.HTTPError:
                continue
            for job in resp.json():
                title = job.get("text", "")
                if not looks_like_swe_internship(title):
                    continue
                cats = job.get("categories", {}) or {}
                out.append(
                    RawDetection(
                        source=Source.LEVER,
                        company=company_from_slug(slug),
                        title=title,
                        url=job.get("hostedUrl", ""),
                        date_posted=parse_epoch(job.get("createdAt")),
                        locations=[cats.get("location", "")],
                        payload={"lever_id": job.get("id")},
                    )
                )
        return out
