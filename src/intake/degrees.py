"""Heuristic degree-level classification. No LLM.

First pass over title + page text. The verifier's verdict, when present,
overrides this. Empty result means "no requirement found" — the frontend
treats that as open to all levels.
"""

from __future__ import annotations

import re

import httpx

from .schema import DegreeLevel

# Full words match anywhere but every token is word-bounded: an unbounded
# "master" matched "McMaster University" in application-form school
# dropdowns and tagged whole boards MS; unbounded "doctoral" matched
# "postdoctoral". The two-letter abbreviations are ambiguous (CSS
# -ms-flex, "multiple sclerosis (MS)" in EEOC boilerplate), so they are
# hyphen-guarded AND require degree context nearby. PhD needs word
# boundaries: bare ph\s?d matches "graph data". Covers PhD, Ph.D., Ph. D.,
# PhDs, DPhil, D.Phil, Doctor of Philosophy; MS covers Master('s),
# Masters, M.Sc, MSc, M.Eng; BS covers Bachelor('s), Bachelors, B.Sc,
# BSc, undergrad(uate)(s).
PATTERNS: dict[DegreeLevel, re.Pattern] = {
    "BS": re.compile(
        r"\bbachelor(?:['’]?s)?\b|\bb\.?sc\b|\bundergrad(?:uate)?s?\b",
        re.IGNORECASE,
    ),
    "MS": re.compile(
        r"\bmaster(?:['’]?s)?\b|\bm\.?sc\b|\bm\.?eng\b",
        re.IGNORECASE,
    ),
    "PhD": re.compile(
        r"\bph\.?\s?d\.?s?\b|\bd\.?\s?phil\b|\bdoctor of philosophy\b"
        r"|\bdoctoral\b|\bdoctorates?\b",
        re.IGNORECASE,
    ),
}
ABBREV_PATTERNS: dict[DegreeLevel, re.Pattern] = {
    "BS": re.compile(r"(?<![\w-])b\.?s\.?(?![\w-])", re.IGNORECASE),
    "MS": re.compile(r"(?<![\w-])m\.?s\.?(?![\w-])", re.IGNORECASE),
}
DEGREE_CONTEXT_RE = re.compile(
    r"degree|pursuing|enrolled|education|graduat|university|student"
    r"|computer science|engineering|\bscience\b",
    re.IGNORECASE,
)
# Application-form boilerplate (EEOC self-identification etc.) mentions
# medical conditions and demographics; never classify from it.
BOILERPLATE_RE = re.compile(
    r"voluntary self-identification|equal employment opportunity"
    r"|eeo is the law|demographic questions",
    re.IGNORECASE,
)

SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
# Two public Workday URL shapes, same CXS detail endpoint on the same host.
# The locale segment (en-US, fr-FR) is optional in both:
#   {tenant}.wdN.myworkdayjobs.com/{locale?}/{site}/job/...   tenant in host
#   wdN.myworkdaysite.com/{locale?}/recruiting/{tenant}/{site}/job/...
_WD_LOCALE = r"(?:[a-zA-Z]{2}-[a-zA-Z]{2}/)?"
WORKDAY_JOBS_RE = re.compile(
    rf"https://([^/]+\.myworkdayjobs\.com)/{_WD_LOCALE}([^/]+)(/job/.+)$"
)
WORKDAY_SITE_RE = re.compile(
    rf"https://(wd\d+\.myworkdaysite\.com)/{_WD_LOCALE}recruiting/([^/]+)/([^/]+)(/job/.+)$"
)


def strip_tags(html: str) -> str:
    """Tags out, and script/style BODIES out too. CSS and JS text is not
    page content; leaving it in poisons every downstream regex."""
    return TAG_RE.sub(" ", SCRIPT_STYLE_RE.sub(" ", html))


def _abbrev_with_context(pat: re.Pattern, text: str, window: int = 120) -> bool:
    return any(
        DEGREE_CONTEXT_RE.search(text[max(0, m.start() - window): m.end() + window])
        for m in pat.finditer(text)
    )


def classify(title: str, page_text: str) -> list[DegreeLevel]:
    """Degree levels the posting is open to, best-effort.

    A degree named in the title is decisive ("PhD Research Intern" -> PhD
    only). Otherwise scan page text: full degree words count anywhere;
    two-letter abbreviations only near degree context.
    """
    def hits(text: str, contextual: bool) -> list[DegreeLevel]:
        out = []
        for lvl, pat in PATTERNS.items():
            if pat.search(text):
                out.append(lvl)
            elif lvl in ABBREV_PATTERNS and (
                ABBREV_PATTERNS[lvl].search(text) if not contextual
                else _abbrev_with_context(ABBREV_PATTERNS[lvl], text)
            ):
                out.append(lvl)
        return out

    in_title = hits(title, contextual=False)
    if in_title:
        return in_title
    m = BOILERPLATE_RE.search(page_text)
    body = page_text[: m.start()] if m else page_text
    return hits(body, contextual=True)


# Pages whose text is application chrome, not a posting description. The
# lever /apply form carries a school dropdown (McMaster University) and
# EEOC prose; greenhouse embed and icims mobile shells carry navigation
# and sibling-job tiles. Matched against the canonical (final) URL.
CHROME_URL_RE = re.compile(
    r"lever\.co/[^\s?#]+/apply(?:[/?#]|$)"
    r"|greenhouse\.io/embed/job_app"
    r"|icims\.com/[^\s]*[?&]mobile=true",
    re.IGNORECASE,
)

# Error shells still return HTTP 200 and fill the page with related-job
# links ("PhD Software Engineer Intern"), which are about other jobs.
ERROR_PAGE_RE = re.compile(
    r"job not found|page not found|position not found"
    r"|no longer available|no longer accepting applications",
    re.IGNORECASE,
)


def degree_page_usable(url: str | None, page_text: str) -> bool:
    """Whether page text is plausibly a posting description that may feed
    degree classification."""
    if url and CHROME_URL_RE.search(url):
        return False
    if page_text and ERROR_PAGE_RE.search(page_text):
        return False
    return True


def classify_posting(
    title: str,
    page_text: str = "",
    qualifications: str | None = None,
    url: str | None = None,
) -> list[DegreeLevel]:
    """classify() with provenance rules for a whole posting.

    1. A degree named in the title stays decisive.
    2. Chrome and error pages (degree_page_usable) never feed page-text
       classification; the qualifications excerpt substitutes, else the
       requirement is unstated.
    3. A degree token inside the qualifications excerpt outranks one
       found elsewhere in page text — requirements beat founder bios and
       sibling-job tiles.
    """
    in_title = classify(title, "")
    if in_title:
        return in_title
    if not degree_page_usable(url, page_text):
        page_text = qualifications or ""
    if qualifications:
        from_quals = classify("", qualifications)
        if from_quals:
            return from_quals
    return classify("", page_text)


def workday_detail_text(url: str, client: httpx.Client) -> str:
    """Workday job pages are JS shells; the description lives on the CXS
    detail endpoint. Derive it from either public URL shape. Empty string
    on any failure or non-Workday URL; the caller then falls back to the
    page text it already has."""
    if m := WORKDAY_JOBS_RE.match(url):
        host, site, path = m.groups()
        tenant = host.split(".")[0]
    elif m := WORKDAY_SITE_RE.match(url):
        host, tenant, site, path = m.groups()
    else:
        return ""
    try:
        resp = client.get(f"https://{host}/wday/cxs/{tenant}/{site}{path}", timeout=15.0)
        resp.raise_for_status()
        desc = resp.json().get("jobPostingInfo", {}).get("jobDescription", "")
    except (httpx.HTTPError, ValueError):
        return ""
    return strip_tags(desc)


QUAL_HEADER_RE = re.compile(
    r"(minimum qualifications|basic qualifications|preferred qualifications"
    r"|required qualifications|qualifications|requirements|what you.ll need"
    r"|what you bring|who you are)[:\s]*",
    re.IGNORECASE,
)


def extract_qualifications(page_text: str, limit: int = 900) -> str | None:
    """First qualifications-like section of the page text, trimmed. Heuristic
    first pass; the verifier can refine later."""
    m = QUAL_HEADER_RE.search(page_text)
    if not m:
        return None
    chunk = page_text[m.start(): m.start() + limit]
    chunk = re.sub(r"[ \t]+", " ", chunk)
    chunk = re.sub(r"\n{3,}", "\n\n", chunk).strip()
    return chunk or None
