"""Audit /api/postings for rows the default board view silently hides.

Replays the Listings default view (status open, degree BS, country
US & Canada, every other filter on all) over a snapshot of the feed and
classifies each excluded row: rejected status, degree mismatch, or
country mismatch. Country mismatches get subclasses:
  parser-gap : locations present but no real country derived (Unknown)
  no-locations : the row carries no location strings at all
  remote-only : derived countries are exactly [Remote]
  foreign : a real non-US, non-Canada country was derived

The country rule is evaluated under both semantics:
  deployed : served countries must contain United States or Canada
  incoming : recompute with the in-tree locations code; remote rows
    (is_remote) and rows with no derived real country pass every region

Rows with no real country whose title or raw location strings still
name US or Canada (including CA province codes) are flagged high
suspicion: the parser missed a hint the row itself states.

Label sanity sweeps over all rows, independent of the default view:
  season : stored season vs a fresh intake.dates.parse_season(title)
  degrees : empty degree_levels vs degree language in qualifications,
    and non-BS labels that neither the title nor the stored
    qualifications support (page-chrome contamination fingerprint)
  category : internship rows whose title or URL matches the event regex
  dates : null date_posted count plus the 10 most recently seen
  drift : served countries vs a local recompute from raw locations

Usage:
  python scripts/audit_filters.py SNAPSHOT.json
  python scripts/audit_filters.py --url https://short-list.app/api/postings

Offline and deterministic given a snapshot. No LLM calls.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from intake.dates import parse_season                      # noqa: E402
from intake.degrees import classify                        # noqa: E402
from intake.locations import countries_of                  # noqa: E402
from intake.triage import EVENT_TITLE_RE, EVENT_URL_RE     # noqa: E402

try:
    from intake.locations import is_remote                 # noqa: E402
except ImportError:  # tree predates the remote axis: Remote lives in countries
    def is_remote(locations: list[str]) -> bool:
        return "Remote" in countries_of(locations)

# Mirrors frontend/src/postings.ts OPEN_STATUSES.
OPEN_STATUSES = {"pending", "gated", "verified", "published"}

NON_COUNTRY = {"Remote", "Unknown"}

# US or Canada hints the location parser may have ignored. Short forms
# are case sensitive on purpose: a lowercase "us" in prose is almost
# always the pronoun, not the country. Province codes cover labels like
# "Calgary, AB" that the state-code table does not know.
US_CA_ABBREV_RE = re.compile(r"\b(?:USA?|U\.S\.A?\.?)\b")
US_CA_WORD_RE = re.compile(r"\b(?:united states|canada|canadian)\b", re.IGNORECASE)
CA_PROVINCE_RE = re.compile(r"\b(?:AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT)\b")

# Degree hints in the canonical URL slug, e.g. ...-intern-bs-summer-2027.
URL_DEGREE_HINT_RE = re.compile(r"\b(?:bs|bachelors?|undergrad(?:uate)?)\b", re.IGNORECASE)

# A stored season year at or below this cannot be a live recruiting
# cycle; it is a page-chrome artifact (copyright line, founding year).
# Bump when the cycles roll over.
STALE_YEAR_MAX = 2025


def degrees_of(p: dict) -> list[str]:
    """Effective degree levels: verdict overrides when non-empty."""
    v = p.get("verdict") or {}
    if v.get("degree_levels"):
        return v["degree_levels"]
    return p.get("degree_levels") or []


def season_of(p: dict) -> str | None:
    """Effective season: verdict overrides."""
    v = p.get("verdict") or {}
    return v.get("season") or p.get("season") or None


def us_ca_hint(p: dict) -> str | None:
    """US or Canada named in the title or raw locations, if anywhere.

    Returns "field: match" for the first hit. Company names count for
    the word forms only ("Canadian Tire" is a strong location hint)."""
    title = p.get("title") or ""
    locs = "; ".join(p.get("locations") or [])
    company = p.get("company") or ""
    for field, text, pats in (
        ("title", title, (US_CA_ABBREV_RE, US_CA_WORD_RE)),
        ("locations", locs, (US_CA_ABBREV_RE, US_CA_WORD_RE, CA_PROVINCE_RE)),
        ("company", company, (US_CA_WORD_RE,)),
    ):
        for pat in pats:
            if m := pat.search(text):
                return f"{field}: {m.group(0)!r}"
    return None


def country_pass_deployed(countries: list[str]) -> bool:
    """Deployed US & Canada region filter over the served countries."""
    return "United States" in countries or "Canada" in countries


def country_pass_incoming(p: dict) -> bool:
    """Incoming US & Canada region filter: recompute from raw locations
    with the in-tree code. Remote rows and unknown-region rows pass."""
    locs = p.get("locations") or []
    if is_remote(locs):
        return True
    cs = [c for c in countries_of(locs) if c not in NON_COUNTRY]
    return not cs or "United States" in cs or "Canada" in cs


def degree_pass(p: dict) -> bool:
    """Default degree filter BS: empty means open to any level."""
    dd = degrees_of(p)
    return not dd or "BS" in dd


def country_subclass(p: dict) -> str:
    """Subclass for a row that fails the deployed country rule."""
    countries = p.get("countries") or []
    locations = [x for x in (p.get("locations") or []) if str(x).strip()]
    real = [c for c in countries if c not in NON_COUNTRY]
    if real:
        return "foreign"
    if not locations:
        return "no-locations"
    if countries and set(countries) <= {"Remote"}:
        return "remote-only"
    return "parser-gap"


def sort_key(p: dict):
    return ((p.get("company") or "").lower(), (p.get("title") or "").lower(), p.get("id") or "")


def clip(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 3] + "..."


def season_compare(stored: str | None, fresh: str | None) -> str | None:
    """Classify stored season vs parse_season(title). None means consistent.

    A bare-year fresh value that the stored season ends with is consistent:
    the stored value came from page text, which is allowed to refine a
    year-only title. Everything else that differs is flagged.
    """
    if stored == fresh:
        return None
    if fresh is None:
        return "title-silent"
    if stored is None:
        return "missing-stored"
    if re.fullmatch(r"20\d{2}", fresh) and stored.endswith(fresh):
        return None
    return "conflict"


def load_rows(args) -> tuple[list[dict], str]:
    if args.url:
        from urllib.request import Request, urlopen

        req = Request(args.url, headers={"User-Agent": "shortlist-audit/1.0"})
        with urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8")), args.url
    if not args.snapshot:
        sys.exit("need a snapshot path or --url")
    path = Path(args.snapshot)
    return json.loads(path.read_text(encoding="utf-8")), str(path)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("snapshot", nargs="?", help="path to an /api/postings JSON snapshot")
    ap.add_argument("--url", help="fetch the feed from this URL instead of a snapshot")
    args = ap.parse_args()

    # Windows consoles default to cp1252; posting titles are UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    rows, source = load_rows(args)
    rows = sorted(rows, key=sort_key)

    # Default-view classification, in the frontend's check order:
    # status, then degree, then country. A row lands in the first bucket
    # it fails. Country analysis below still covers every open row that
    # fails the country rule, even when degree already excluded it.
    in_view_deployed: list[dict] = []
    in_view_incoming: list[dict] = []
    rejected: list[dict] = []
    degree_miss: list[dict] = []
    country_miss_first: list[dict] = []

    country_fail_open: list[dict] = []  # every open row failing deployed country rule

    for p in rows:
        countries = p.get("countries") or []
        if p.get("status") not in OPEN_STATUSES:
            rejected.append(p)
            continue
        deg_ok = degree_pass(p)
        dep_ok = country_pass_deployed(countries)
        inc_ok = country_pass_incoming(p)
        if deg_ok and dep_ok:
            in_view_deployed.append(p)
        if deg_ok and inc_ok:
            in_view_incoming.append(p)
        if not dep_ok:
            country_fail_open.append(p)
        if not deg_ok:
            degree_miss.append(p)
        elif not dep_ok:
            country_miss_first.append(p)

    sub: dict[str, list[dict]] = {"parser-gap": [], "no-locations": [], "remote-only": [], "foreign": []}
    for p in country_fail_open:
        sub[country_subclass(p)].append(p)
    incoming_rescued = [p for p in country_fail_open if country_pass_incoming(p)]

    high_susp = [
        p for p in country_fail_open
        if country_subclass(p) != "foreign" and us_ca_hint(p)
    ]

    print("== Shortlist default-view audit ==")
    print(f"source: {source}")
    print()
    print("-- Counts --")
    print(f"total postings: {len(rows)}")
    print(f"in default view, deployed country rule: {len(in_view_deployed)}")
    print(f"in default view, incoming country rule: {len(in_view_incoming)}")
    print(f"excluded, rejected status (closed tab by design): {len(rejected)}")
    print(f"excluded, degree mismatch (levels without BS): {len(degree_miss)}")
    print(f"excluded, country mismatch (first failing check): {len(country_miss_first)}")
    print(f"open rows failing deployed country rule (any path): {len(country_fail_open)}")
    for name in ("parser-gap", "no-locations", "remote-only", "foreign"):
        print(f"  subclass {name}: {len(sub[name])}")
    print(f"  of those, rescued by the incoming rule: {len(incoming_rescued)}")
    print(f"  still hidden under the incoming rule: {len(country_fail_open) - len(incoming_rescued)}")

    print()
    print("-- High-suspicion country cases (no real country derived, row names US or Canada) --")
    if not high_susp:
        print("none")
    for p in high_susp:
        print(f"* {p.get('company')} | {clip(p.get('title'), 70)}")
        print(f"    hint {us_ca_hint(p)}  served countries: {p.get('countries')}")
        print(f"    raw locations: {p.get('locations')}")

    print()
    print("-- Country parser gaps (locations present, nothing derived but Unknown) --")
    gaps = sub["parser-gap"]
    if not gaps:
        print("none")
    for p in gaps:
        local = countries_of(p.get("locations") or [])
        note = "" if local == (p.get("countries") or []) else f"  local recompute: {local}"
        deg_note = "" if degree_pass(p) else "  (also degree mismatch)"
        print(f"* {p.get('company')} | {clip(p.get('title'), 70)}{deg_note}")
        print(f"    raw locations: {p.get('locations')}{note}")
        print(f"    url: {p.get('canonical_url') or p.get('url')}")

    print()
    print("-- No-locations rows (excluded deployed, pass incoming) --")
    for p in sub["no-locations"]:
        print(f"* {p.get('company')} | {clip(p.get('title'), 70)}")

    print()
    print("-- Foreign rows by derived countries --")
    foreign_counts = Counter(
        tuple(c for c in (p.get("countries") or []) if c not in NON_COUNTRY)
        for p in sub["foreign"]
    )
    for combo, n in sorted(foreign_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {n:3d}  {', '.join(combo)}")

    print()
    print("-- Degree exclusions (open rows, effective levels without BS) --")
    # Split by evidence. A degree named in the title is decisive and
    # trustworthy. Otherwise the label came from full page text at gate
    # time; when the stored qualifications excerpt does not back it, the
    # usual culprit is page chrome (nav, related jobs, error shells).
    title_decisive: list[dict] = []
    quals_backed: list[dict] = []
    unsupported: list[dict] = []
    for p in degree_miss:
        levels = set(degrees_of(p))
        if set(classify(p.get("title") or "", "")) & levels:
            title_decisive.append(p)
            continue
        quals = p.get("qualifications") or ""
        if quals.strip() and set(classify("", quals)) & levels:
            quals_backed.append(p)
        else:
            unsupported.append(p)
    print(f"title states the level (trustworthy): {len(title_decisive)}")
    print(f"qualifications excerpt backs the level: {len(quals_backed)}")
    print(f"UNSUPPORTED label (neither title nor stored quals back it): {len(unsupported)}")
    for p in unsupported:
        url = p.get("canonical_url") or p.get("url") or ""
        hint = "  URL HINTS BS" if URL_DEGREE_HINT_RE.search(url.replace("-", " ").replace("/", " ")) else ""
        quals = (p.get("qualifications") or "").strip()
        ev = "no quals stored" if not quals else f"quals imply {classify('', quals) or 'nothing'}"
        print(f"* {p.get('company')} | {clip(p.get('title'), 64)} | stored: {degrees_of(p)} | {ev}{hint}")
        print(f"    url: {url}")

    print()
    print("-- Season disagreements (stored vs parse_season on title) --")
    season_flags: dict[str, list[tuple[dict, str | None, str | None]]] = {
        "conflict": [], "missing-stored": [], "title-silent": [],
    }
    for p in rows:
        stored = p.get("season") or None
        fresh = parse_season(p.get("title") or "")
        kind = season_compare(stored, fresh)
        if kind:
            season_flags[kind].append((p, stored, fresh))
    for kind in ("conflict", "missing-stored"):
        items = season_flags[kind]
        print(f"{kind}: {len(items)}")
        for p, stored, fresh in items:
            print(f"* {p.get('company')} | {clip(p.get('title'), 70)}")
            print(f"    stored: {stored!r}  parse_season(title): {fresh!r}")
    silent = season_flags["title-silent"]
    print(f"title-silent (stored season, title parses to none; page text or source stated it): {len(silent)}")
    for p, stored, fresh in silent[:5]:
        print(f"  e.g. {p.get('company')} | {clip(p.get('title'), 60)} | stored: {stored!r}")
    stale = []
    for p in rows:
        s = p.get("season") or ""
        m = re.search(r"20\d\d", s)
        if m and int(m.group(0)) <= STALE_YEAR_MAX:
            stale.append((p, s))
    print(f"stale-year seasons (year <= {STALE_YEAR_MAX}, page-chrome artifacts): {len(stale)}")
    for p, s in stale:
        print(f"* {p.get('company')} | {clip(p.get('title'), 64)} | stored season: {s!r}")

    print()
    print("-- Degree language in qualifications while degree_levels is empty --")
    deg_lang = []
    for p in rows:
        if p.get("degree_levels"):
            continue
        quals = p.get("qualifications") or ""
        if not quals.strip():
            continue
        found = classify("", quals)
        if found:
            deg_lang.append((p, found))
    print(f"count: {len(deg_lang)}")
    for p, found in deg_lang:
        print(f"* {p.get('company')} | {clip(p.get('title'), 70)} | quals imply: {found}")

    print()
    print("-- Category suspects (internship rows whose title or URL matches the event regex) --")
    cat_susp = []
    for p in rows:
        if (p.get("category") or "internship") != "internship":
            continue
        title = p.get("title") or ""
        url = p.get("canonical_url") or p.get("url") or ""
        if EVENT_TITLE_RE.search(title) or EVENT_URL_RE.search(url):
            cat_susp.append(p)
    print(f"count: {len(cat_susp)}")
    for p in cat_susp:
        print(f"* {p.get('company')} | {clip(p.get('title'), 70)} | status: {p.get('status')}")
        print(f"    url: {p.get('canonical_url') or p.get('url')}")

    print()
    print("-- Null date_posted (shown as detection-relative freshness) --")
    no_date = [p for p in rows if not p.get("date_posted")]
    print(f"count: {len(no_date)}")
    recent = sorted(no_date, key=lambda p: p.get("first_seen") or "", reverse=True)[:10]
    for p in recent:
        print(f"* {p.get('company')} | {clip(p.get('title'), 60)} | first_seen: {p.get('first_seen')} | text: {p.get('date_posted_text')!r}")

    print()
    print("-- Migration delta (served real countries vs in-tree recompute) --")
    # Remote and Unknown are buckets of the deployed derivation, not real
    # countries; the in-tree code moved them to other axes. Compare real
    # countries only, so this section shows genuine derivation changes.
    gains: list[tuple[dict, list[str], list[str]]] = []
    losses: list[tuple[dict, list[str], list[str]]] = []
    for p in rows:
        served_real = sorted(c for c in (p.get("countries") or []) if c not in NON_COUNTRY)
        local = sorted(c for c in countries_of(p.get("locations") or []) if c not in NON_COUNTRY)
        if served_real == local:
            continue
        (gains if set(local) > set(served_real) else losses).append((p, served_real, local))
    print(f"rows gaining real countries under the in-tree code: {len(gains)}")
    for p, served_real, local in gains[:10]:
        print(f"  + {p.get('company')} | {clip(p.get('title'), 56)} | {served_real} -> {local}")
    print(f"rows losing or changing real countries under the in-tree code: {len(losses)}")
    for p, served_real, local in losses[:10]:
        print(f"  - {p.get('company')} | {clip(p.get('title'), 56)} | {served_real} -> {local}  locations: {p.get('locations')}")

    print()
    print("-- Still countryless under the in-tree code despite a US or Canada hint --")
    # These rows stay visible in the incoming default view (unknown region
    # passes), but their country label is still wrong: the row itself
    # names US or Canada and the parser derives nothing.
    residual = []
    for p in rows:
        if p.get("status") not in OPEN_STATUSES:
            continue
        locs = p.get("locations") or []
        if not locs:
            continue
        local = [c for c in countries_of(locs) if c not in NON_COUNTRY]
        if not local and us_ca_hint(p):
            residual.append(p)
    print(f"count: {len(residual)}")
    for p in residual:
        print(f"* {p.get('company')} | {clip(p.get('title'), 64)} | hint {us_ca_hint(p)}")
        print(f"    locations: {p.get('locations')}")


if __name__ == "__main__":
    main()
