"""Pipeline orchestrator.

Flow per cycle:
  1. Poll all detectors.
  2. Upsert detections into the store (dedupe happens here).
  3. New postings run the rule gate. Fail -> REJECTED. Pass -> GATED.
  4. Live postings sharing a canonical URL reconcile into the earliest
     record when an ATS job key or near-equal titles prove one posting;
     the extras are REJECTED as duplicates.
  5. GATED postings go to the verifier agent. Verdict sets VERIFIED or REJECTED.
  6. VERIFIED postings are handed to the publisher hook, then every matching
     subscription gets ONE notification carrying the whole batch (notify.py).

No posting reaches the publisher without a stored verdict.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import unquote, urlsplit

import httpx

from .config import Settings, load_notify_settings
from .dates import parse_iso, parse_season
from .detectors import (
    BROWSER_DETECTORS,
    CUSTOM_DETECTORS,
    AshbyDetector,
    Detector,
    GithubListDetector,
    GreenhouseDetector,
    LeverDetector,
    MarkdownListDetector,
    OpportunityListDetector,
    SuggestionDetector,
    WorkdayDetector,
)
from .notify import Sender, notify_new_postings
from .schema import Posting, Status
from .senders import build_sender, send_pending_confirmations
from .store import Store, open_store
from .verify import VerifierAgent, run_rules

log = logging.getLogger("intake")


@dataclass
class CycleReport:
    detected: int = 0
    new: int = 0
    rule_rejected: int = 0
    merged: int = 0
    collapsed: int = 0  # canonical URLs holding 3+ rows that would not unify
    agent_rejected: int = 0
    verified: int = 0
    published: int = 0
    errors: list[str] = field(default_factory=list)


def default_publisher(p: Posting) -> None:
    """Placeholder publish hook. Replace with the web-app API call."""
    log.info("PUBLISH %s — %s (%s)", p.company, p.title, p.url)


_SEASON_TOKEN_RE = re.compile(
    r"^(?:fall|autumn|spring|summer|winter|20\d{2}|'\d{2})$", re.IGNORECASE
)
_SEASON_SEP_CHARS = " \t.,;:|/()[]{}-–—"  # dashes: ascii, en, em


def has_trailing_season(title: str) -> bool:
    """True when the title ends in a season phrase ("Intern, Dynamo - Fall 2026").

    parse_season stays the authority on reading the phrase; the token check
    pins it to the end, so a leading season ("Fall 2026 SWE Intern") or a
    mid-phrase year ("2026 Cohort") does not count.
    """
    words = title.split()
    for n in (2, 1):  # try "Fall 2026" before a bare "Fall" or "2026"
        if len(words) <= n:
            continue
        tail = words[-n:]
        if all(_SEASON_TOKEN_RE.match(w.strip(_SEASON_SEP_CHARS)) for w in tail):
            return parse_season(" ".join(tail)) is not None
    return False


# Per-requisition identifiers the ATS platforms put in posting URLs. A key
# match is strong identity; its absence proves nothing (generic board pages
# carry none, which is exactly how distinct reqs end up sharing one
# canonical_url when redirects flatten them).
_WORKDAY_KEY_RE = re.compile(r"/job/.*_([A-Za-z]{1,3}\d+|\d{5,})/?$")
_GREENHOUSE_KEY_RE = re.compile(r"/jobs/(\d+)(?:/|$)")
_UUID_KEY_RE = re.compile(
    r"([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})"  # lever, ashby
)


def extract_job_key(url: str | None) -> tuple[str, str] | None:
    """(host, ATS job key) when the URL carries a per-req identifier, else None.

    Workday req tails (Title_JR2022295, also R1234 and plain 5+ digit ids),
    Greenhouse /jobs/<digits>, Lever and Ashby UUIDs in the path. No
    pattern means no key, never a guess.
    """
    if not url:
        return None
    parts = urlsplit(url)
    path = unquote(parts.path)
    for pat in (_WORKDAY_KEY_RE, _GREENHOUSE_KEY_RE, _UUID_KEY_RE):
        m = pat.search(path)
        if m:
            return parts.netloc.lower(), m.group(1).lower()
    return None


_SEASON_WORDS = {"fall", "autumn", "spring", "summer", "winter"}
_YEAR_TOKEN_RE = re.compile(r"^(?:20\d{2}|\d{2})$")


def fold_title(title: str) -> frozenset[str]:
    """Token set of a title with role-neutral noise folded away.

    Casefold, one token for engineer/engineering and for intern/internship
    variants including co-op, punctuation to spaces, seasons and years
    dropped. Two titles fold equal only when nothing role-distinguishing
    separates them: "Software Engineer Intern" == "Software Engineering
    Internship - Fall 2026", but a "- C++ or Python" tail keeps its tokens
    and blocks the match.
    """
    s = title.casefold()
    s = re.sub(r"\bco[\s-]?op\b", "intern", s)
    s = re.sub(r"\bintern(?:ship)?s?\b", "intern", s)
    s = re.sub(r"\bengineering\b", "engineer", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return frozenset(
        t for t in s.split()
        if t not in _SEASON_WORDS and not _YEAR_TOKEN_RE.match(t)
    )


def merge_duplicate(survivor: Posting, duplicate: Posting) -> None:
    """Fold a duplicate record of one posting into its survivor.

    Union what both records saw, keep the earliest date_posted, fill
    single-valued fields only where the survivor lacks them. The survivor
    keeps its own title unless it carries a trailing season suffix and the
    duplicate's does not; the suffix-free spelling is the cleaner one.
    The duplicate keeps its row for history, rejected with a pointer back.
    """
    if has_trailing_season(survivor.title) and not has_trailing_season(duplicate.title):
        survivor.title = duplicate.title
    for src in duplicate.sources:
        if src not in survivor.sources:
            survivor.sources.append(src)
    for loc in duplicate.locations:
        if loc and loc not in survivor.locations:
            survivor.locations.append(loc)
    if duplicate.date_posted and (
        survivor.date_posted is None or duplicate.date_posted < survivor.date_posted
    ):
        survivor.date_posted = duplicate.date_posted
    if survivor.season is None:
        survivor.season = duplicate.season
    if not survivor.degree_levels:
        survivor.degree_levels = duplicate.degree_levels
    if survivor.qualifications is None:
        survivor.qualifications = duplicate.qualifications
    if survivor.verdict is None:
        survivor.verdict = duplicate.verdict
    duplicate.status = Status.REJECTED
    duplicate.reject_reason = f"duplicate of {survivor.id}"


def reconcile_duplicates(
    store: Store,
    on_merge: Callable[[Posting, Posting], None] | None = None,
    on_collapse: Callable[[list[Posting]], None] | None = None,
    apply: bool = True,
) -> tuple[int, int]:
    """Collapse live records that resolved to one canonical URL.

    The dedupe key is company + title, so a source that embeds the season
    in the title mints a second key for a posting already tracked under its
    plain spelling. A shared canonical_url is the candidate signal, not
    proof: gate redirects flatten some sites' distinct reqs onto one
    generic page. Two tiers of identity within a shared-URL group:

      strong: same host and same ATS job key (extract_job_key over
        canonical_url, falling back to the detected url) merges regardless
        of titles.
      weak: when no row in the group has any key, all rows merge only if
        every title folds to one token set (fold_title). Distinct keys, or
        key evidence on one side only, block the weak tier entirely.

    A group of 3+ rows that will not unify is a collapsed canonical: no
    weak-tier merges happen in it (strong-tier folds inside still do), and
    it is counted and reported via on_collapse for review each cycle.
    Earliest first_seen survives any fold. Rejected duplicates leave the
    live set, so the pass is idempotent. on_merge fires per fold;
    apply=False computes without writing (the backfill script's dry run).
    Returns (duplicates folded, collapsed groups).
    """
    merged = 0
    collapsed = 0

    def fold(survivor: Posting, dups: list[Posting]) -> None:
        nonlocal merged
        for dup in dups:
            merge_duplicate(survivor, dup)
            if on_merge:
                on_merge(survivor, dup)
            if apply:
                store.update(dup)
            merged += 1
        if apply:
            store.update(survivor)

    for group in store.duplicate_groups():
        group.sort(key=lambda p: p.first_seen)
        by_key: dict[tuple[str, str], list[Posting]] = {}
        keyless: list[Posting] = []
        for p in group:
            key = extract_job_key(p.canonical_url) or extract_job_key(p.url)
            if key:
                by_key.setdefault(key, []).append(p)
            else:
                keyless.append(p)
        survivors = 0
        for rows in by_key.values():  # strong tier, per req id
            if len(rows) > 1:
                fold(rows[0], rows[1:])
            survivors += 1
        if keyless:
            # not by_key means keyless is the whole group, two rows or more
            if not by_key and len({fold_title(p.title) for p in keyless}) == 1:
                fold(keyless[0], keyless[1:])  # weak tier, all or nothing
                survivors += 1
            else:
                survivors += len(keyless)
        if survivors > 1 and len(group) >= 3:
            collapsed += 1
            if on_collapse:
                on_collapse(group)
    return merged, collapsed


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        store: Store | None = None,
        detectors: list[Detector] | None = None,
        verifier: VerifierAgent | None = None,
        publisher: Callable[[Posting], None] = default_publisher,
        sender: Sender | None = None,
    ):
        self.settings = settings
        self.store = store or open_store(settings.db_path)
        wl = settings.watchlist
        self.detectors: list[Detector] = detectors or [
            SuggestionDetector(self.store),
            GreenhouseDetector(wl.greenhouse),
            LeverDetector(wl.lever),
            AshbyDetector(wl.ashby),
            WorkdayDetector(wl.workday),
            *[CUSTOM_DETECTORS[name]() for name in wl.custom if name in CUSTOM_DETECTORS],
            *[BROWSER_DETECTORS[name]() for name in wl.browser if name in BROWSER_DETECTORS],
            GithubListDetector(wl.github_lists, max_age_days=settings.list_max_age_days),
            OpportunityListDetector(wl.opportunity_lists),
            MarkdownListDetector(wl.markdown_lists),
        ]
        self.verifier = verifier or VerifierAgent(settings)
        self.publisher = publisher
        # One delivery config per process, not per cycle. build_sender gives
        # the SES sender where boto3 and a region exist and the LogSender
        # placeholder everywhere else, so a laptop cycle stays offline.
        self.notify = load_notify_settings()
        self.sender = sender or build_sender(self.notify, store=self.store)
        self.http = httpx.Client(
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )

    def run_cycle(self, verify: bool = True) -> CycleReport:
        """One full pass. verify=False stops after the rule gate — postings
        park at GATED. Use it to inspect acquisition without spending on the
        verifier (no API key required)."""
        report = CycleReport()

        # 1-2: detect and dedupe
        for det in self.detectors:
            try:
                detections = det.poll()
            except Exception as e:  # a detector crash must not kill the cycle
                report.errors.append(f"{det.name}: {e}")
                continue
            report.detected += len(detections)
            for d in detections:
                _, is_new = self.store.upsert_detection(d)
                report.new += int(is_new)

        # 3: rule gate + canonical URL resolution
        for p in self.store.by_status(Status.PENDING):
            gate = run_rules(p, self.http)
            if gate.reject_reason:
                p.status, p.reject_reason = Status.REJECTED, gate.reject_reason
                report.rule_rejected += 1
            else:
                p.canonical_url = gate.canonical_url
                p.degree_levels = gate.degree_levels
                p.qualifications = gate.qualifications
                p.season = p.season or gate.season
                p.status = Status.GATED
            self.store.update(p)

        # 4: reconcile duplicate identities (ATS job key, else near-equal
        # titles). canonical_url exists only after the gate, so the pass
        # sits here; one grouped query per cycle.
        report.merged, report.collapsed = reconcile_duplicates(
            self.store,
            on_merge=lambda s, d: log.info(
                "MERGE %s: kept %r, rejected %r", s.company, s.title, d.title
            ),
            on_collapse=lambda g: log.info(
                "COLLAPSED %s: %d rows share %s",
                g[0].company, len(g), g[0].canonical_url,
            ),
        )

        # Unconfirmed email subscriptions expire. This sits above the
        # no-verify return on purpose: production runs --no-verify, and an
        # address nobody confirmed must still stop being stored there.
        pruned = self.store.prune_unverified()
        if pruned:
            log.info("PRUNE %d unverified subscription(s)", pruned)

        # Signups taken while the email channel was dark get the confirmation
        # they were promised. A no-op until sending is configured, and above
        # the no-verify return for the same reason as the prune.
        send_pending_confirmations(self.store, self.sender)

        if not verify:
            return report

        # 5: agent verification
        for p in self.store.by_status(Status.GATED):
            try:
                verdict = self.verifier.verify(p)
            except Exception as e:
                report.errors.append(f"verify {p.id}: {e}")
                continue  # stays GATED, retried next cycle
            p.verdict = verdict
            if p.date_posted is None and verdict.date_posted:
                p.date_posted = parse_iso(verdict.date_posted)
            if verdict.season:
                p.season = verdict.season
            if verdict.approved:
                p.status = Status.VERIFIED
                report.verified += 1
            else:
                p.status = Status.REJECTED
                p.reject_reason = "; ".join(verdict.reasons)[:500]
                report.agent_rejected += 1
            self.store.update(p)

        # 6: publish, then one notification per subscriber for the whole
        # batch. Fanning out per posting would turn a twenty-posting cycle
        # into twenty emails to the same inbox.
        published: list[Posting] = []
        for p in self.store.by_status(Status.VERIFIED):
            try:
                self.publisher(p)
            except Exception as e:
                report.errors.append(f"publish {p.id}: {e}")
                continue
            p.status = Status.PUBLISHED
            self.store.update(p)
            report.published += 1
            published.append(p)

        if published:
            try:
                notify_new_postings(
                    self.store.active_subscriptions(), published, self.sender,
                    self.store, self.notify.daily_cap,
                )
            except Exception as e:  # a sender crash must not kill the cycle
                report.errors.append(f"notify: {e}")

        return report
