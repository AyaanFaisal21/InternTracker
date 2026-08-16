"""Verifier agent. Final gate before a posting is publishable.

Method: fetch the posting page, strip it to text, and ask Claude for a
structured verdict against the detection metadata. The verdict schema is
enforced by the API (structured outputs), so there is no free-text parsing.

The agent decides what rules cannot: whether the page actually describes an
open, legitimate SWE internship, or a closed/ghost/mislabeled posting that
still returns HTTP 200.
"""

from __future__ import annotations

import re

import anthropic
import httpx

from ..config import Settings
from ..schema import Posting, Verdict

TAG_RE = re.compile(r"<script[^>]*>.*?</script>|<style[^>]*>.*?</style>|<[^>]+>", re.DOTALL)

SYSTEM = """You verify job postings for a Rutgers CS internship feed. \
You receive detection metadata and the text of the posting page. Decide:
- is_swe_internship: the role is a software/CS-adjacent internship or co-op \
(SWE, ML, data, infra, security, etc.), not new-grad, not full-time, not a \
non-technical role.
- is_open: the page shows an active application. Pages saying "no longer \
accepting applications", "position filled", or redirecting to a generic \
careers page are not open.
- is_legitimate: a real employer posting. Reject staffing-agency reposts, \
pay-to-apply schemes, brand-ambassador roles, and pages whose content does \
not match the claimed company/title.
- season: the recruiting season if stated (e.g. "Summer 2026"), else null.
- date_posted: the posting date if the page states one, as YYYY-MM-DD, \
else null. Never infer a date that is not on the page.
- degree_levels: every degree level the posting accepts, from the page text. \
"Currently pursuing a BS or MS" -> ["BS", "MS"]. "PhD students only" -> \
["PhD"]. If the page states no degree requirement, return all three.
Be strict: students act on these within minutes of publication. When the page \
text contradicts the metadata, trust the page."""


class VerifierAgent:
    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None):
        self.settings = settings
        self.client = client or anthropic.Anthropic()
        self.http = httpx.Client(
            timeout=settings.page_fetch_timeout,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Shortlist intake verifier)"},
        )

    def fetch_page_text(self, url: str) -> str:
        try:
            resp = self.http.get(url)
            resp.raise_for_status()
        except httpx.HTTPError:
            return ""
        text = TAG_RE.sub(" ", resp.text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[: self.settings.max_page_chars]

    def verify(self, p: Posting) -> Verdict:
        target = p.canonical_url or p.url
        page_text = self.fetch_page_text(target)
        prompt = (
            f"Detection metadata:\n"
            f"- company: {p.company}\n"
            f"- title: {p.title}\n"
            f"- url: {target}\n"
            f"- locations: {', '.join(p.locations) or 'unknown'}\n"
            f"- sources: {', '.join(s.value for s in p.sources)}\n\n"
            f"Posting page text (empty means the fetch failed; judge from "
            f"metadata alone and lower confidence):\n{page_text or '(fetch failed)'}"
        )
        response = self.client.messages.parse(
            model=self.settings.verifier_model,
            max_tokens=2048,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=Verdict,
        )
        return response.parsed_output
