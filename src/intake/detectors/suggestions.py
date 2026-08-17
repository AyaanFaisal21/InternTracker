"""Suggestion detector: human-in-the-loop intake.

Two kinds, both submitted from the dashboard:

  url:     a direct posting link, including links not yet indexed anywhere
           public. Human-vouched, so the intern title prefilter is skipped.
           The page still passes the rule gate and verifier before publish.
  company: a company or keyword to investigate. Probed against the public
           ATS APIs (greenhouse/lever/ashby) under likely slugs; hits emit
           detections and report the board so it can join the watchlist.

A probe miss is the normal case, not a verdict: companies with a standard
ATS board are the ones already covered by the watchlist, and a visitor names
a company precisely because it is not. So when the probe misses, tier two
(resolve.py) asks Claude with web search for the employer's real careers page
and current postings. That tier costs money per call and is triggered by a
public endpoint, so it is optional (no ANTHROPIC_API_KEY, or
INTAKE_RESOLVER=off, means probe-only) and guarded by a cache, a per-cycle
cap, and a daily budget.

Suggestions resolve to matched / no_match / error with a result note shown on
the dashboard. A suggestion held back by a spend cap keeps its 'new' status
and carries a note saying so, so the next cycle picks it up again.
"""

from __future__ import annotations

import logging
import re

import httpx

from ..normalize import unwrap_redirector
from ..resolve import (
    CompanyResolution,
    GuardedResolver,
    build_resolver,
    clean_company,
    valid_company,
)
from ..schema import RawDetection, Source
from ..store import Store
from .base import INTERN_RE, looks_like_swe_internship

log = logging.getLogger("intake")

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

# Distinguishes "build the default resolver" from "run without one". None has
# to mean off, because that is what the tests and a probe-only deployment ask
# for; a default of None would silently spend money the moment a key exists.
AUTO = object()

NO_RESOLVER = (
    "no public greenhouse/lever/ashby board, and deep search is off; "
    "queued for review"
)
NOT_RESOLVED = "no public postings found yet; queued for review"


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

    def __init__(
        self,
        store: Store,
        client: httpx.Client | None = None,
        resolver: GuardedResolver | None | object = AUTO,
    ):
        self.store = store
        self.client = client or httpx.Client(
            timeout=20.0, follow_redirects=True, headers={"User-Agent": BROWSER_UA}
        )
        self.resolver = build_resolver(store) if resolver is AUTO else resolver

    def poll(self) -> list[RawDetection]:
        out: list[RawDetection] = []
        if self.resolver is not None:
            self.resolver.begin_cycle()  # per-cycle cap: a burst spreads over cycles
        for sug in self.store.pending_suggestions():
            try:
                if sug["kind"] == "url":
                    dets, status, result = self._process_url(sug)
                else:
                    dets, status, result = self._process_company(sug)
            except Exception as e:
                dets, status, result = [], "error", f"{type(e).__name__}: {e}"
            if status is None:  # held back by a spend cap; stays queued
                self.store.defer_suggestion(sug["id"], result)
                continue
            self.store.resolve_suggestion(sug["id"], status, result)
            out.extend(dets)
        return out

    def _process_url(self, sug: dict) -> tuple[list[RawDetection], str, str]:
        url = unwrap_redirector(sug["value"].strip())
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

    def _process_company(self, sug: dict) -> tuple[list[RawDetection], str | None, str]:
        keywords = [k.strip().lower() for k in (sug.get("keywords") or "").split(",") if k.strip()]
        company = clean_company(sug["value"])
        hit = self._probe(company, keywords)
        if hit is not None:  # cheap, free, deterministic: right where it works
            return hit
        if self.resolver is None:
            return [], "no_match", NO_RESOLVER
        if not valid_company(company):
            # The endpoint refuses these now, but rows queued before it did
            # are still in the table and must not reach a paid call.
            return [], "no_match", "not a resolvable company name"
        try:
            attempt = self.resolver.resolve(company, keywords)
        except Exception as e:  # a resolver fault must not break the cycle
            log.warning("resolver failed for %r: %s", company, e)
            return [], "error", f"deep search failed ({type(e).__name__}); resubmit to retry"
        if attempt.deferred:
            return [], None, attempt.deferred
        return self._from_resolution(company, attempt.resolution, keywords, attempt.cached)

    def _probe(self, company: str, keywords: list[str]) -> tuple[list[RawDetection], str, str] | None:
        for slug in slugify(company):
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
                dets = self._extract(family, slug, jobs, keywords, company)
                note = (
                    f"found on {family} as '{slug}' ({len(jobs)} roles, "
                    f"{len(dets)} matched); add to watchlist"
                )
                return dets, "matched", note
        return None

    def _from_resolution(
        self, company: str, res: CompanyResolution, keywords: list[str], cached: bool
    ) -> tuple[list[RawDetection], str, str]:
        dets = []
        for p in res.postings:
            # No SWE title prefilter here, unlike the board probe. The
            # resolver was asked for exactly these roles, and the prefilter
            # would drop the titles this tier exists to catch ("Summer
            # Analyst - Technology"). The rule gate and verifier still run.
            if keywords and not any(_kw_match(k, p.title.lower()) for k in keywords):
                continue
            dets.append(
                RawDetection(
                    source=Source.SUGGESTION,
                    company=company,
                    title=p.title,
                    url=p.url,
                    payload={
                        "resolver": True,
                        "careers_url": res.careers_url,
                        "ats_family": res.ats_family,
                        "ats_slug": res.ats_slug,
                        "confidence": res.confidence,
                    },
                )
            )
        origin = "cached" if cached else "deep search"
        board = f"; {res.watchlist_hint}" if res.watchlist_hint else ""
        if dets:
            return dets, "matched", (
                f"{len(dets)} posting(s) queued from {res.careers_url or 'careers page'}"
                f"{board} [{origin}]"
            )
        if res.careers_url:
            return [], "matched", (
                f"careers page found: {res.careers_url}; no open SWE internship "
                f"listed right now{board} [{origin}]"
            )
        return [], "no_match", f"{NOT_RESOLVED} [{origin}]"

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
