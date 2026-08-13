"""Markdown-table list detector (zapplyjobs style).

Curated README repos with sectioned tables:

  ## Internships
  | Name | Status/Open Date | Year | Note |
  | [Dropbox SWE intern](https://...) | Open | Sophomore | ... |

Section heading maps to category; Year column feeds audience tags; rows
without a link or not marked open are skipped. Curated audience, so no
SWE title prefilter — the gate and triage classify downstream.
"""

from __future__ import annotations

import re

import httpx

from ..schema import RawDetection, Source

LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
HEADING_RE = re.compile(r"^#+\s*(.+?)\s*$")

SECTION_CATEGORY = {
    "internships": "internship",
    "winternships": "internship",
    "fellowships": "program",
    "internship-matching fellowships": "program",
    "externships / insight series": "program",
    "special programs & resources": "program",
}


class MarkdownListDetector:
    name = "markdown_list"

    def __init__(self, readme_urls: list[str], client: httpx.Client | None = None):
        self.readme_urls = readme_urls
        self.client = client or httpx.Client(timeout=30.0, follow_redirects=True)

    def poll(self) -> list[RawDetection]:
        out: list[RawDetection] = []
        for url in self.readme_urls:
            try:
                resp = self.client.get(url)
                resp.raise_for_status()
            except httpx.HTTPError:
                continue
            out.extend(self.parse(resp.text))
        return out

    def parse(self, md: str) -> list[RawDetection]:
        out: list[RawDetection] = []
        category = None
        season = None
        for line in md.splitlines():
            h = HEADING_RE.match(line)
            if h:
                name = h.group(1).strip().lower()
                category = SECTION_CATEGORY.get(name)
                season = "Winter" if name == "winternships" else None
                continue
            if category is None or not line.strip().startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 3 or cells[0].lower() == "name" or set(cells[0]) <= {"-", " "}:
                continue
            m = LINK_RE.search(cells[0])
            if not m:
                continue  # no link, nothing to validate or publish
            status = cells[1].lower()
            if "open" not in status:
                continue  # '?', closed, or dated-future rows wait
            title, link = m.group(1).strip(), m.group(2)
            year = cells[2].lower() if len(cells) > 2 else ""
            audience = (
                ["underclassmen"]
                if any(w in year for w in ("fresh", "soph", "first", "second", "all"))
                else []
            )
            out.append(
                RawDetection(
                    source=Source.OPPORTUNITY_LIST,
                    company=title.split()[0] if title else "",
                    title=title,
                    url=link,
                    category=category,
                    audience=audience,
                    season=season,
                    payload={"year_note": cells[2] if len(cells) > 2 else None,
                             "note": cells[3] if len(cells) > 3 else None},
                )
            )
        return out
