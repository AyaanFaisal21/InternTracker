"""Suggestion detector: human-in-the-loop intake.

Two kinds, both submitted from the dashboard:

  url:     a direct posting link, including links not yet indexed anywhere
           public. Human-vouched, so the intern title prefilter is skipped.
           The page still passes the rule gate and verifier before publish.
  company: a company or keyword to investigate. Probed against the public
           ATS APIs (greenhouse/lever/ashby) under likely slugs; hits emit
           detections and report the board so it can join the watchlist.

Suggestions resolve to matched / no_match / error with a result note shown
on the dashboard.
"""

from __future__ import annotations

import re

import httpx

from ..schema import RawDetection, Source
from ..store import Store
from .base import INTERN_RE, looks_like_swe_internship

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

ATS_PROBES = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}


def _kw_match(k: str, title_lower: str) -> bool:
    """Whole-word keyword match. "intern" must not match "International",
    but should cover interns/internship(s), so the intern family routes
    through the canonical INTERN_RE."""
    if k in ("intern", "interns", "internship", "internships", "co-op", "coop"):
        return bool(INTERN_RE.search(title_lower))
    return bool(re.search(rf"\b{re.escape(k)}s?\b", title_lower))


def slugify(company: str) -> list[str]:
    """Likely board slugs, most-specific first. Companies register under
    styled names (Anduril -> andurilindustries), so try common suffixes.
    A suggestion probe is human-triggered; ~20 requests is acceptable."""
    base = re.sub(r"[^a-z0-9 ]", "", company.lower()).strip()
    words = base.split()
    joined = "".join(words)
    dashed = "-".join(words)
    variants = [joined, dashed]
    if len(words) > 1:  # also try the first word alone ("Anduril Industries" -> anduril)
        variants.append(words[0])
    variants += [joined + suf for suf in ("industries", "hq", "inc", "labs")]
    return list(dict.fromkeys(v for v in variants if v))


class SuggestionDetector:
    name = "suggestion"

    def __init__(self, store: Store, client: httpx.Client | None = None):
        self.store = store
        self.client = client or httpx.Client(
            timeout=20.0, follow_redirects=True, headers={"User-Agent": BROWSER_UA}
        )

    def poll(self) -> list[RawDetection]:
        out: list[RawDetection] = []
        for sug in self.store.pending_suggestions():
            try:
                if sug["kind"] == "url":
                    dets, status, result = self._process_url(sug)
                else:
                    dets, status, result = self._process_company(sug)
            except Exception as e:
                dets, status, result = [], "error", f"{type(e).__name__}: {e}"
            self.store.resolve_suggestion(sug["id"], status, result)
            out.extend(dets)
        return out

    def _process_url(self, sug: dict) -> tuple[list[RawDetection], str, str]:
        url = sug["value"].strip()
        try:
            resp = self.client.get(url)
        except httpx.HTTPError as e:
            return [], "error", f"unreachable: {type(e).__name__}"
        if resp.status_code >= 400 and resp.status_code not in (403, 429):
            return [], "no_match", f"url returned {resp.status_code}"
        m = TITLE_RE.search(resp.text or "")
        page_title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
        company = sug.get("company") or httpx.URL(url).host.split(".")[-2].title()
        det = RawDetection(
            source=Source.SUGGESTION,
            company=company,
            title=page_title or url,
            url=url,
            payload={"suggestion_id": sug["id"]},
        )
        return [det], "matched", f"ingested as {company}: {det.title[:80]}"

    def _process_company(self, sug: dict) -> tuple[list[RawDetection], str, str]:
        keywords = [k.strip().lower() for k in (sug.get("keywords") or "").split(",") if k.strip()]
        for slug in slugify(sug["value"]):
            for family, tmpl in ATS_PROBES.items():
                try:
                    resp = self.client.get(tmpl.format(slug=slug))
                except httpx.HTTPError:
                    continue
                if resp.status_code != 200:
                    continue
                try:
                    data = resp.json()
                except ValueError:
                    continue
                jobs = data.get("jobs", data if isinstance(data, list) else [])
                if not jobs:
                    continue
                dets = self._extract(family, slug, jobs, keywords, sug["value"])
                note = (
                    f"found on {family} as '{slug}' ({len(jobs)} roles, "
                    f"{len(dets)} matched); add to watchlist"
                )
                return dets, "matched", note
        return [], "no_match", "no public greenhouse/lever/ashby board found"

    def _extract(self, family, slug, jobs, keywords, company) -> list[RawDetection]:
        out = []
        for job in jobs:
            title = job.get("title") or job.get("text") or ""
            url = job.get("absolute_url") or job.get("hostedUrl") or job.get("jobUrl") or ""
            if not title or not url:
                continue
            if keywords:
                if not any(_kw_match(k, title.lower()) for k in keywords):
                    continue
            elif not looks_like_swe_internship(title):
                continue
            out.append(
                RawDetection(
                    source=Source.SUGGESTION,
                    company=company,
                    title=title,
                    url=url,
                    payload={"probe_family": family, "probe_slug": slug},
                )
            )
        return out
