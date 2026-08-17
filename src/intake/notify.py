"""Notification fan-out for newly published postings.

Visitors subscribe to new-posting alerts, optionally narrowed to named
companies. Everything here is channel-agnostic and ends at the Sender
seam; LogSender is the placeholder default, the same pattern as
pipeline.py's default_publisher, and senders.EmailSender is the live
channel behind it.

Three rules shape this module.

Matching is exact after normalization, never partial. Tolerance comes from
folding the spellings of one company together (corporate suffixes,
well-known renames), not from loosening the comparison: "apple" as a
prefix of "Apple Bank" would mail people about jobs they never asked for,
which is the one failure a notification system cannot recover from. The
cost is that a half-remembered name matches nothing, and /api/companies is
the answer to that: the subscriber picks from names that provably have
live postings.

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


def build_filters(companies: list[str]) -> dict:
    """The stored filter shape: what the subscriber typed, plus the keys
    the fan-out compares on.

    Both are kept because they answer different questions. The keys decide
    delivery; the verbatim names let the UI show someone their own wording
    back instead of our normalization of it. An empty list means every
    company, which is the '{}' filter the store already understands.
    """
    if not companies:
        return {}
    return {
        "companies": list(companies),
        "company_keys": sorted({company_key(c) for c in companies if company_key(c)}),
    }


def _wanted_keys(filters: dict) -> set[str]:
    """Keys a subscription asked for. Rows written before company_keys
    existed still carry only the verbatim names, so those are folded here."""
    if "company_keys" in filters:
        return {k for k in filters["company_keys"] if k}
    return {k for k in (company_key(c) for c in filters.get("companies") or []) if k}


def _matches(filters: dict, posting: Posting) -> bool:
    """Empty or missing companies list means every company matches."""
    wanted = _wanted_keys(filters)
    return not wanted or company_key(posting.company) in wanted


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
