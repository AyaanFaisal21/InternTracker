"""URL canonicalization.

Goal: every published link points at the employer's own posting page.
Aggregator links (simplify.jobs, redirect shorteners) are resolved by
following redirects. Tracking parameters are stripped. ATS-functional
parameters (job ids like ashby_jid, lever posting ids) are kept.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import httpx

# Exact-match query keys to drop. Prefix match handles utm_*.
TRACKING_KEYS = {"ref", "referrer", "source", "src", "gh_src", "gclid", "fbclid", "lever-source"}

# Social redirector hosts wrap the real URL in a query param. Postings and
# events shared on Instagram/Facebook arrive like this.
REDIRECTOR_HOSTS = {"l.instagram.com", "l.facebook.com", "lm.facebook.com", "l.messenger.com"}


def unwrap_redirector(url: str) -> str:
    parts = urlsplit(url)
    if parts.netloc.lower() in REDIRECTOR_HOSTS:
        for k, v in parse_qsl(parts.query):
            if k == "u" and v.startswith("http"):
                return unquote(v)
    return url


def strip_tracking(url: str) -> str:
    parts = urlsplit(url)
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_KEYS
    ]
    return urlunsplit(parts._replace(query=urlencode(kept), fragment=""))


def resolve_canonical(
    url: str, client: httpx.Client
) -> tuple[str | None, str | None, str]:
    """Follow redirects to the final employer page.

    Returns (reject_reason, canonical_url, page_text). Reason and canonical
    are mutually exclusive; page_text is the response body ("" on reject).
    """
    if not url.startswith("http"):
        return "malformed url", None, ""
    url = unwrap_redirector(url)
    try:
        resp = client.get(url, follow_redirects=True, timeout=15.0)
    except httpx.HTTPError as e:
        return f"url unreachable: {type(e).__name__}", None, ""
    if resp.status_code >= 400:
        return f"url returned {resp.status_code}", None, ""
    return None, strip_tracking(str(resp.url)), resp.text
