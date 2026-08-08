"""Custom career-site detectors: companies with no standard ATS board.

Each adapter targets what the company's own careers frontend consumes:

  google:    SSR HTML on the results page — stable job links, no JS needed.
  microsoft: public gcsservices JSON API.

Deferred to the browser-render tier (their APIs are token-gated and their
SSR pages ignore search filters): apple, tiktok/bytedance, meta.
"""

from __future__ import annotations

import re

import httpx

from ..schema import RawDetection, Source
from .base import looks_like_swe_internship

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

GOOGLE_URL = "https://www.google.com/about/careers/applications/jobs/results"
GOOGLE_LINK_RE = re.compile(r'href="jobs/results/(\d+)-([a-z0-9-]+)')
GOOGLE_QUERIES = ["software intern", "software engineering intern"]
GOOGLE_PAGES = 3

MS_URL = "https://gcsservices.careers.microsoft.com/search/api/v1/search"


def _client(client: httpx.Client | None) -> httpx.Client:
    return client or httpx.Client(
        timeout=25.0, follow_redirects=True, headers={"User-Agent": BROWSER_UA}
    )


class GoogleCareersDetector:
    name = "google_careers"

    def __init__(self, client: httpx.Client | None = None):
        self.client = _client(client)

    def poll(self) -> list[RawDetection]:
        seen: dict[str, RawDetection] = {}
        for q in GOOGLE_QUERIES:
            for page in range(1, GOOGLE_PAGES + 1):
                try:
                    resp = self.client.get(GOOGLE_URL, params={"q": q, "page": page})
                    resp.raise_for_status()
                except httpx.HTTPError:
                    break
                links = GOOGLE_LINK_RE.findall(resp.text)
                if not links:
                    break
                for job_id, slug in links:
                    title = slug.replace("-", " ").title()
                    if job_id in seen or not looks_like_swe_internship(title):
                        continue
                    seen[job_id] = RawDetection(
                        source=Source.CUSTOM,
                        company="Google",
                        title=title,
                        url=f"https://www.google.com/about/careers/applications/jobs/results/{job_id}-{slug}",
                        payload={"job_id": job_id},
                    )
        return list(seen.values())


class MicrosoftDetector:
    name = "microsoft"

    def __init__(self, client: httpx.Client | None = None):
        self.client = _client(client)

    def poll(self) -> list[RawDetection]:
        out: list[RawDetection] = []
        fetched = 0
        for pg in range(1, 6):
            try:
                resp = self.client.get(
                    MS_URL,
                    params={
                        "q": "software intern", "l": "en_us", "pg": pg,
                        "pgSz": 20, "o": "Relevance", "flt": "true",
                    },
                )
                resp.raise_for_status()
                result = resp.json().get("operationResult", {}).get("result", {})
                jobs = result.get("jobs", [])
            except (httpx.HTTPError, ValueError):
                break
            if not jobs:
                break
            fetched += len(jobs)
            for job in jobs:
                title = job.get("title", "")
                if not looks_like_swe_internship(title):
                    continue
                job_id = job.get("jobId", "")
                props = job.get("properties", {})
                locs = props.get("locations") or [props.get("primaryLocation", "")]
                out.append(
                    RawDetection(
                        source=Source.CUSTOM,
                        company="Microsoft",
                        title=title,
                        url=f"https://jobs.careers.microsoft.com/global/en/job/{job_id}/",
                        locations=[loc for loc in locs if loc],
                        payload={"job_id": job_id},
                    )
                )
            if fetched >= result.get("totalJobs", 0):
                break
        return out


CUSTOM_DETECTORS = {
    "google": GoogleCareersDetector,
    "microsoft": MicrosoftDetector,
}
