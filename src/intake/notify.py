"""Notification fan-out for newly published postings.

Visitors subscribe to new-posting alerts, optionally narrowed to named
companies. The delivery channel (web push vs email) is deliberately
undecided, so everything here is channel-agnostic and ends at the Sender
seam; LogSender is the placeholder default, the same pattern as
pipeline.py's default_publisher. A real sender plugs in per channel
without touching the matching logic: web push needs a VAPID keypair and
the endpoint JSON stored in target, email needs SMTP credentials and the
address stored in target.
"""

from __future__ import annotations

import logging
from typing import Protocol

from .schema import Posting, norm_text

log = logging.getLogger("intake")


class Sender(Protocol):
    """Delivery seam: called once per matching subscription per posting."""

    def __call__(self, sub: dict, posting: Posting) -> None: ...


class LogSender:
    """Placeholder sender: one log line per delivery. Logs the subscription
    id, never the target, so addresses stay out of the logs."""

    def __call__(self, sub: dict, posting: Posting) -> None:
        log.info(
            "NOTIFY %s subscription %s: %s (%s)",
            sub["channel"], sub["id"], posting.title, posting.company,
        )


def _matches(filters: dict, posting: Posting) -> bool:
    """Empty or missing companies list means every company matches."""
    companies = filters.get("companies") or []
    if not companies:
        return True
    want = norm_text(posting.company)
    return any(norm_text(c) == want for c in companies)


def notify_new_posting(subs: list[dict], posting: Posting, sender: Sender) -> int:
    """Send to every matching subscription. Returns the send count."""
    sent = 0
    for sub in subs:
        if _matches(sub.get("filters") or {}, posting):
            sender(sub, posting)
            sent += 1
    return sent
