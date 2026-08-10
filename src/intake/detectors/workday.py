"""Workday detector. Polls the per-tenant CXS JSON API — no auth required.

Workday looks non-uniform (each company gets its own subdomain and career
site name) but every tenant serves the same endpoint:

  POST https://{host}/wday/cxs/{tenant}/{site}/jobs
  body: {"appliedFacets": {}, "limit": N, "offset": N, "searchText": "intern"}

Job URL: https://{host}/en-US/{site}{externalPath}
Req ID:  bulletFields[0] (e.g. "JR2012398") — the stable posting identity.

One adapter, per-company config. Covers NVIDIA, Qualcomm, Adobe, Salesforce,
and most large employers not on Greenhouse/Lever/Ashby.
"""

from __future__ import annotations

import httpx

from ..config import WorkdayBoard
from ..dates import parse_workday_relative
from ..schema import RawDetection, Source
from .base import looks_like_swe_internship

PAGE_SIZE = 20
MAX_PAGES = 50  # per board per cycle; searchText matches body text too, so
                # totals run high (NVIDIA: ~900) — scan them all, title-filter here


class WorkdayDetector:
    name = "workday"

    def __init__(self, boards: list[WorkdayBoard], client: httpx.Client | None = None):
        self.boards = boards
        self.client = client or httpx.Client(
            timeout=20.0,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )

    def poll(self) -> list[RawDetection]:
        out: list[RawDetection] = []
        for b in self.boards:
            out.extend(self._poll_board(b))
        return out

    def _poll_board(self, b: WorkdayBoard) -> list[RawDetection]:
        api = f"https://{b.host}/wday/cxs/{b.tenant}/{b.site}/jobs"
        out: list[RawDetection] = []
        for page in range(MAX_PAGES):
            body = {
                "appliedFacets": {},
                "limit": PAGE_SIZE,
                "offset": page * PAGE_SIZE,
                "searchText": "intern",
            }
            try:
                resp = self.client.post(api, json=body)
                resp.raise_for_status()
            except httpx.HTTPError:
                break  # one bad board must not stop the sweep
            data = resp.json()
            postings = data.get("jobPostings", [])
            for job in postings:
                title = job.get("title", "")
                if not looks_like_swe_internship(title):
                    continue
                path = job.get("externalPath", "")
                bullets = job.get("bulletFields", [])
                out.append(
                    RawDetection(
                        source=Source.WORKDAY,
                        company=b.company,
                        title=title,
                        url=f"https://{b.host}/en-US/{b.site}{path}",
                        locations=[job.get("locationsText", "")],
                        date_posted=(posted := parse_workday_relative(job.get("postedOn"))),
                        date_posted_text=None if posted else job.get("postedOn"),
                        payload={
                            "req_id": bullets[0] if bullets else None,
                            "posted_on": job.get("postedOn"),
                        },
                    )
                )
            if (page + 1) * PAGE_SIZE >= data.get("total", 0) or not postings:
                break
        return out
