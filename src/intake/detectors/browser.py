"""Browser-render detector tier.

For career sites whose data is token-gated or client-rendered. A headless
Chromium mimics a person: load the page, type the query, submit, then
capture the JSON the site's own frontend receives. The site handles its
own tokens; we never reverse them.

Playwright is an optional dependency:
  pip install -e '.[browser]' && playwright install chromium

Parsing is split from fetching so parsers stay fixture-testable without
a browser.

apple: URL params are ignored by the SPA; only UI-driven search filters.
The search box POST to /api/v1/search is captured. Titles are umbrella
postings per degree level ("Software Undergrad Engineering Internships").
TODO: tiktok/bytedance, meta.
"""

from __future__ import annotations

from ..schema import RawDetection, Source
from .base import looks_like_swe_internship

APPLE_SEARCH_PAGE = "https://jobs.apple.com/en-us/search"
APPLE_QUERY = "software engineering internship"
APPLE_SEARCH_BOX = 'input[placeholder="Search by role or keyword"]'


def parse_apple_results(res: dict) -> list[RawDetection]:
    out: list[RawDetection] = []
    for j in res.get("searchResults", []):
        title = j.get("postingTitle", "")
        pos_id = j.get("positionId", "")
        slug = j.get("transformedPostingTitle", "")
        if not (title and pos_id and slug):
            continue
        if not looks_like_swe_internship(title):
            continue
        locs = j.get("locations") or []
        out.append(
            RawDetection(
                source=Source.CUSTOM,
                company="Apple",
                title=title,
                url=f"https://jobs.apple.com/en-us/details/{pos_id}/{slug}",
                locations=[loc.get("name", "") for loc in locs if isinstance(loc, dict)],
                payload={"position_id": pos_id, "req_id": j.get("reqId")},
            )
        )
    return out


def capture_apple_search(timeout_ms: int = 45_000) -> dict:
    """Drive the search UI, return the /api/v1/search response body."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "browser tier needs playwright: pip install -e '.[browser]' "
            "&& playwright install chromium"
        ) from e
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(APPLE_SEARCH_PAGE, wait_until="networkidle", timeout=timeout_ms)
            with page.expect_response(
                lambda r: "/api/v1/search" in r.url and r.request.method == "POST",
                timeout=timeout_ms,
            ) as info:
                page.fill(APPLE_SEARCH_BOX, APPLE_QUERY)
                page.keyboard.press("Enter")
            data = info.value.json()
            return data.get("res", data)
        finally:
            browser.close()


class AppleBrowserDetector:
    name = "apple_browser"

    def poll(self) -> list[RawDetection]:
        return parse_apple_results(capture_apple_search())


BROWSER_DETECTORS = {
    "apple": AppleBrowserDetector,
}
