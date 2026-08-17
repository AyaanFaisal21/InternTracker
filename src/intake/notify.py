"""Notification fan-out for newly published postings.

Visitors subscribe to new-posting alerts, optionally narrowed to named
companies, degree levels and countries. Everything here is
channel-agnostic and ends at the Sender seam; LogSender is the placeholder
default, the same pattern as pipeline.py's default_publisher, and
senders.EmailSender is the live channel behind it.

Four rules shape this module.

Matching is exact after normalization, never partial. Tolerance comes from
folding the spellings of one company together (corporate suffixes,
well-known renames), not from loosening the comparison: "apple" as a
prefix of "Apple Bank" would mail people about jobs they never asked for,
which is the one failure a notification system cannot recover from. The
cost is that a half-remembered name matches nothing, and /api/companies is
the answer to that: the subscriber picks from names that provably have
live postings.

Absence of evidence never hides a posting. A posting that states no degree
level, one whose location labels name no country, and one marked remote
all pass a filter on that dimension instead of failing it. Our
classification relaxes a label it cannot support rather than guessing, so
the unstated case is common; reading it as "matches nothing" would mail a
subscriber a fraction of what they asked for and look like a dead feed.
The board already narrows on exactly these rules, so the alert and the
page a subscriber checks agree.

Sends are batched per cycle, not per posting. Twenty detections in one
poll cycle is one message per subscriber, and a durable per-subscriber
daily ceiling bounds the rest.

Email needs a confirmed opt-in before anything is sent to it (see
store.verify_by_token). A push endpoint carries its own proof, because the
browser mints it only after the visitor grants permission.
"""

from __future__ import annotations

import logging
from typing import Protocol

from .locations import countries_of, is_remote
from .schema import Posting, norm_text

log = logging.getLogger("intake")

# Legal-form tails that say nothing about which employer is meant. Stripped
# from the end of a name so "NVIDIA Corp" and "NVIDIA" are one company.
CORP_SUFFIXES = frozenset({
    "inc", "corp", "corporation", "llc", "ltd", "plc", "co",
    "holdings", "group", "technologies", "labs",
})

# Renames the world still uses both sides of. Deliberately tiny: every
# entry is a company that changed its own name, never a guess at what a
# subscriber might have meant.
COMPANY_ALIASES = {
    "facebook": "meta",
    "alphabet": "google",
    "twitter": "x",
}


def company_key(name: str) -> str:
    """Comparison key for one company name.

    norm_text folds case and punctuation, trailing legal-form words are
    dropped, and a known rename resolves to one side. Nothing else: no
    prefix, substring or edit-distance matching, so the key of "apple" is
    never the key of "Apple Bank".
    """
    words = norm_text(name).split()
    while len(words) > 1 and words[-1] in CORP_SUFFIXES:
        words.pop()
    key = " ".join(words)
    return COMPANY_ALIASES.get(key, key)


def build_filters(
    companies: list[str],
    degrees: list[str] | None = None,
    countries: list[str] | None = None,
) -> dict:
    """The stored filter shape: what the subscriber picked, plus the keys
    the fan-out compares on.

    Companies carry two entries because they answer different questions.
    The keys decide delivery; the verbatim names let the UI show someone
    their own wording back instead of our normalization of it. Degrees and
    countries need no such pair: both are picked from fixed lists, so what
    the subscriber chose is already what the fan-out compares.

    Only dimensions that narrow are written. An empty one is left out
    entirely, so a subscription to everything is still the '{}' filter the
    store has always understood, and every key present means a constraint.
    """
    filters: dict = {}
    if companies:
        filters["companies"] = list(companies)
        filters["company_keys"] = sorted(
            {company_key(c) for c in companies if company_key(c)}
        )
    if degrees:
        filters["degrees"] = list(degrees)
    if countries:
        filters["countries"] = list(countries)
    return filters


def _wanted_keys(filters: dict) -> set[str]:
    """Keys a subscription asked for. Rows written before company_keys
    existed still carry only the verbatim names, so those are folded here."""
    if "company_keys" in filters:
        return {k for k in filters["company_keys"] if k}
    return {k for k in (company_key(c) for c in filters.get("companies") or []) if k}


def _degrees_of(posting: Posting) -> list[str]:
    """Degree levels the posting is open to. The verifier read the page, so
    its answer wins over the rule gate's title heuristic whenever it states
    one; this is the precedence the board renders and filters on."""
    if posting.verdict and posting.verdict.degree_levels:
        return list(posting.verdict.degree_levels)
    return list(posting.degree_levels)


def _matches(filters: dict, posting: Posting) -> bool:
    """One posting against one subscription: AND across dimensions, OR
    within each. A missing or empty dimension constrains nothing, so a
    subscription written before that dimension existed behaves exactly as
    it did, and '{}' still matches everything.

    Three absences match rather than hide, and each is deliberate:

      - a posting stating no degree level matches every degree filter,
        because the classifier relaxes a label it cannot support to nothing
        instead of guessing at one;
      - a posting whose labels name no country matches every country
        filter, because an unparsed location is not evidence of a place;
      - a remote posting matches every country filter, because it is
        workable from any of them.

    The board narrows the same way, so a subscriber who follows an alert
    onto the page finds the posting still there.
    """
    wanted = _wanted_keys(filters)
    if wanted and company_key(posting.company) not in wanted:
        return False
    degrees = filters.get("degrees") or []
    if degrees:
        stated = _degrees_of(posting)
        if stated and not any(d in stated for d in degrees):
            return False
    countries = filters.get("countries") or []
    if countries:
        derived = countries_of(posting.locations)
        if (
            derived
            and not is_remote(posting.locations)
            and not any(c in derived for c in countries)
        ):
            return False
    return True


def _deliverable(sub: dict) -> bool:
    """Email waits for the double opt-in click; any other channel does not.

    A push endpoint is minted by the browser after the visitor granted
    permission, so consent is already proven. An address is typed into a
    form by whoever is at the keyboard, which may not be its owner.
    """
    return sub.get("channel") != "email" or bool(sub.get("verified"))


class Sender(Protocol):
    """Delivery seam: called once per subscription per cycle, carrying every
    posting that subscription matched."""

    def __call__(self, sub: dict, postings: list[Posting]) -> None: ...


class LogSender:
    """Placeholder sender: one log line per delivery. Logs the subscription
    id, never the target, so addresses stay out of the logs."""

    def __call__(self, sub: dict, postings: list[Posting]) -> None:
        log.info(
            "NOTIFY %s subscription %s: %d posting(s), first %s (%s)",
            sub["channel"], sub["id"], len(postings),
            postings[0].title, postings[0].company,
        )


def notify_new_postings(
    subs: list[dict],
    postings: list[Posting],
    sender: Sender,
    store=None,
    daily_cap: int = 0,
) -> int:
    """One send per matching subscription for the whole cycle's batch.

    Returns the number of subscriptions sent to. store plus a non-zero
    daily_cap turn on the durable per-subscriber ceiling: a subscription
    that already had daily_cap sends today is skipped, so a detection burst
    spread across cycles cannot flood one inbox. A send counts even when
    the transport failed, which is the safe direction: a broken channel
    must not turn into a retry loop against the same address.
    """
    capped = bool(daily_cap) and store is not None
    sent = 0
    for sub in subs:
        if not _deliverable(sub):
            continue
        mine = [p for p in postings if _matches(sub.get("filters") or {}, p)]
        if not mine:
            continue
        if capped and store.notify_sends_today(sub["id"]) >= daily_cap:
            log.info("NOTIFY-CAP subscription %s: %d today", sub["id"], daily_cap)
            continue
        sender(sub, mine)
        sent += 1
        if capped:
            store.count_notify_send(sub["id"])
    return sent
