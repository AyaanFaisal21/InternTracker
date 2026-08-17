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

# The Name cell is "<company> <program/role words>" with no company
# column, so the company is the prefix before the first program-ish
# keyword. Keywords must follow whitespace: a leading keyword stays part
# of the name ("Explore Program" does not empty out).
_PROGRAM_CUT_RE = re.compile(
    r"\s+(?:swe|sde|software|engineer\w*|developer\w*|intern\w*"
    r"|winternship\w*|co[\s-]?op\b|fellow\w*|program\w*|scholar\w*"
    r"|externship\w*|apprentice\w*|women(?:['’]?s)?|discovery|explore"
    r"|insight|fttp|api)\b",
    re.IGNORECASE,
)


def company_from_title(title: str) -> str:
    """Company display name from a curated Name cell.

    "Jane Street FTTP" -> "Jane Street"; "Coding it Forward's Fellowship"
    -> "Coding it Forward"; a name with no program keyword ("Year Up",
    "NASA") is itself the company. Never the old first-word cut, which
    produced "Jane", "Coding", "Year", "Emma"."""
    m = _PROGRAM_CUT_RE.search(title)
    name = title[: m.start()] if m else title
    name = name.strip(" \t-–—,:;")
    name = re.sub(r"['’]s?$", "", name).strip()
    return name or title.strip()

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
                    company=company_from_title(title),
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
