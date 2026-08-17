"""Notification backbone: subscription store roundtrip, company matching,
the double opt-in gate, and the batched publish-time fan-out. Senders are
capture callables; nothing leaves the process.

The matching table is the load-bearing part. A false positive mails someone
about a job they never asked for, and a false negative leaves them waiting
on silence, so both directions are asserted by name.
"""

from unittest.mock import patch

import pytest

from intake.config import Settings, Watchlist
from intake.notify import (
    build_filters,
    company_key,
    notify_new_postings,
)
from intake.pipeline import Pipeline
from intake.schema import Posting, RawDetection, Source, Status, Verdict
from intake.store import Store
from intake.verify.rules import GateResult


def make_posting(company="Stripe", title="Software Engineer Intern"):
    return Posting.from_detection(RawDetection(
        source=Source.GREENHOUSE, company=company, title=title,
        url="https://x.co/1",
    ))


def sub(sid, companies=None, channel="email", verified=True):
    return {"id": sid, "channel": channel, "target": f"s{sid}@x.edu",
            "verified": verified, "token": f"tok{sid}",
            "filters": build_filters(companies or [])}


# -- store roundtrip ------------------------------------------------------

def test_subscription_store_roundtrip(tmp_path):
    store = Store(tmp_path / "t.db")
    sid1, token1, verify1 = store.add_subscription(
        "email", "a@b.edu", build_filters(["NVIDIA"]))
    sid2, token2, verify2 = store.add_subscription(
        "push", '{"endpoint": "https://p.example/x"}', {})
    assert sid1 != sid2 and token1 != token2 and verify1 != verify2
    assert len(token1) == 32 and len(verify1) == 32
    assert token1 != verify1  # the unsubscribe secret is not the verify secret

    subs = store.active_subscriptions()
    assert [s["id"] for s in subs] == [sid1, sid2]
    # Verbatim spelling for display, keys for delivery, decoded not text.
    assert subs[0]["filters"] == {"companies": ["NVIDIA"], "company_keys": ["nvidia"]}
    assert subs[1]["filters"] == {}
    assert subs[0]["channel"] == "email" and subs[1]["target"].startswith("{")
    assert subs[0]["verified"] is False  # nothing is sent until it is True

    assert store.verify_by_token(verify1) is True
    assert store.active_subscriptions()[0]["verified"] is True
    # Idempotent: a double-tapped link must not look broken.
    assert store.verify_by_token(verify1) is True
    assert store.verify_by_token("not-a-token") is False

    assert store.deactivate_by_token(token1) is True
    assert [s["id"] for s in store.active_subscriptions()] == [sid2]
    assert store.deactivate_by_token("not-a-token") is False


def test_unverified_email_subscriptions_expire(tmp_path):
    store = Store(tmp_path / "p.db")
    stale, _, _ = store.add_subscription("email", "stale@x.edu", {})
    fresh, _, _ = store.add_subscription("email", "fresh@x.edu", {})
    confirmed, _, verify = store.add_subscription("email", "ok@x.edu", {})
    pushed, _, _ = store.add_subscription("push", '{"endpoint": "x"}', {})
    store.verify_by_token(verify)
    store.conn.execute(
        "UPDATE subscriptions SET created_at = datetime('now', '-8 days') "
        "WHERE id IN (?,?,?)", (stale, confirmed, pushed))
    store.conn.commit()

    # Only the address that never confirmed goes. A confirmed row is wanted,
    # and push has no confirmation step to fail.
    assert store.prune_unverified() == 1
    assert sorted(s["id"] for s in store.active_subscriptions()) == [
        fresh, confirmed, pushed]
    assert store.prune_unverified() == 0


def test_daily_send_counter_is_per_subscriber_and_durable(tmp_path):
    db = tmp_path / "c.db"
    store = Store(db)
    sid, _, _ = store.add_subscription("email", "a@b.edu", {})
    assert store.notify_sends_today(sid) == 0
    assert store.count_notify_send(sid) == 1
    assert store.count_notify_send(sid) == 2
    assert store.notify_sends_today(sid + 1) == 0
    # Durable: the poller restarts on every deploy, and an in-process count
    # would hand a fresh allowance to anyone who can make it restart.
    assert Store(db).notify_sends_today(sid) == 2


# -- matching -------------------------------------------------------------

# (what the subscriber picked, the posting's company, should it match)
MATCH_CASES = [
    ("NVIDIA", "NVIDIA", True),
    ("nvidia.", "NVIDIA", True),
    ("NVIDIA Corp", "NVIDIA", True),                     # suffix stripped
    ("NVIDIA", "NVIDIA Corporation", True),              # and in reverse
    ("Palantir Technologies, Inc.", "Palantir", True),   # two suffixes
    ("Stripe Inc", "Stripe, Inc.", True),
    ("Facebook", "Meta", True),                          # alias
    ("Meta", "Facebook", True),
    ("Alphabet", "Google", True),
    ("Twitter", "X", True),
    # The conservative half. "goldman" not matching "Goldman Sachs" is the
    # designed behavior, not a gap: partial matching is what would put
    # Apple Bank postings in an Apple subscriber's inbox. Autocomplete off
    # /api/companies is how a subscriber lands on the full name instead.
    ("goldman", "Goldman Sachs", False),
    ("apple", "Apple Bank", False),
    ("Apple Bank", "Apple", False),
    ("Googel", "Google", False),                         # typo, not a rename
    ("Meta", "Meta Bank", False),
]


@pytest.mark.parametrize("picked,posted,expected", MATCH_CASES)
def test_company_matching_table(picked, posted, expected):
    sends = []
    n = notify_new_postings([sub(1, [picked])], [make_posting(posted)],
                            lambda s, ps: sends.append(s["id"]))
    assert (n == 1) is expected and (sends == [1]) is expected
    assert (company_key(picked) == company_key(posted)) is expected


def test_empty_filters_match_every_company():
    sends = []
    n = notify_new_postings([sub(1), sub(2)], [make_posting("Anthropic")],
                            lambda s, ps: sends.append(s["id"]))
    assert n == 2 and sends == [1, 2]


def test_build_filters_keeps_the_wording_and_the_key():
    assert build_filters([]) == {}
    assert build_filters(["NVIDIA Corp", "stripe."]) == {
        "companies": ["NVIDIA Corp", "stripe."],
        "company_keys": ["nvidia", "stripe"],
    }


def test_rows_written_before_company_keys_existed_still_match():
    sends = []
    legacy = {"id": 9, "channel": "push", "target": "x", "verified": False,
              "token": "t9", "filters": {"companies": ["NVIDIA Corp"]}}
    n = notify_new_postings([legacy], [make_posting("NVIDIA")],
                            lambda s, ps: sends.append(s["id"]))
    assert n == 1 and sends == [9]


# -- opt-in gate ----------------------------------------------------------

def test_unverified_email_gets_nothing_and_push_is_unaffected():
    sends = []
    subs = [sub(1, verified=False), sub(2, verified=True),
            sub(3, channel="push", verified=False)]
    n = notify_new_postings(subs, [make_posting()],
                            lambda s, ps: sends.append(s["id"]))
    # 1 never confirmed. 3 is a push endpoint the browser minted only after
    # the visitor granted permission, so its consent is already proven.
    assert n == 2 and sends == [2, 3]


# -- batching and the daily cap -------------------------------------------

def test_one_send_per_subscriber_carries_every_matching_posting():
    calls = []
    postings = [make_posting("Stripe"), make_posting("NVIDIA"),
                make_posting("Stripe", "ML Intern")]
    subs = [sub(1), sub(2, ["Stripe"]), sub(3, ["Datadog"])]
    n = notify_new_postings(subs, postings,
                            lambda s, ps: calls.append((s["id"], len(ps))))
    # One call each, never one per posting; the subscriber with no matches
    # is not mailed an empty message.
    assert n == 2 and calls == [(1, 3), (2, 2)]


def test_daily_cap_holds_across_cycles(tmp_path):
    store = Store(tmp_path / "cap.db")
    sid, _, verify = store.add_subscription("email", "a@b.edu", {})
    store.verify_by_token(verify)
    subs = store.active_subscriptions()
    calls = []

    for _ in range(5):
        notify_new_postings(subs, [make_posting()],
                            lambda s, ps: calls.append(s["id"]), store, 3)
    assert len(calls) == 3
    assert store.notify_sends_today(sid) == 3
    # Uncapped is the default the matching tests use, so the cap can only
    # ever be a deliberate setting.
    notify_new_postings(subs, [make_posting()], lambda s, ps: calls.append(s["id"]))
    assert len(calls) == 4


# -- pipeline fan-out -----------------------------------------------------

class FakeDetector:
    name = "fake"

    def __init__(self, detections):
        self.detections = detections

    def poll(self):
        return self.detections


class FakeVerifier:
    def verify(self, p):
        return Verdict(
            is_swe_internship=True, is_open=True, is_legitimate=True,
            confidence="high", reasons=[],
        )


DET = RawDetection(
    source=Source.GREENHOUSE, company="Stripe",
    title="Software Engineer Intern", url="https://stripe.com/jobs/1",
)
DET2 = RawDetection(
    source=Source.GREENHOUSE, company="Stripe",
    title="ML Engineer Intern", url="https://stripe.com/jobs/2",
)
DET3 = RawDetection(
    source=Source.GREENHOUSE, company="NVIDIA",
    title="Software Engineer Intern", url="https://nvidia.com/jobs/3",
)


def make_pipeline(tmp_path, store, sender, detections=(DET,)):
    return Pipeline(
        settings=Settings(watchlist=Watchlist(), db_path=tmp_path / "t.db"),
        store=store,
        detectors=[FakeDetector(list(detections))],
        verifier=FakeVerifier(),
        publisher=lambda p: None,
        sender=sender,
    )


def confirmed(store, target, filters):
    sid, _, verify = store.add_subscription("email", target, filters)
    store.verify_by_token(verify)
    return sid


@patch("intake.pipeline.run_rules",
       return_value=GateResult(canonical_url="https://stripe.com/jobs/1", degree_levels=["BS"]))
def test_publish_fans_out_once_per_matching_subscription(_rules, tmp_path):
    store = Store(tmp_path / "t.db")
    confirmed(store, "all@x.edu", {})
    confirmed(store, "stripe@x.edu", build_filters(["stripe!"]))
    confirmed(store, "nv@x.edu", build_filters(["NVIDIA"]))
    store.add_subscription("email", "unconfirmed@x.edu", {})  # never clicked
    sends = []
    pipeline = make_pipeline(tmp_path, store, lambda s, ps: sends.append(s["target"]))

    report = pipeline.run_cycle()
    assert report.published == 1 and report.errors == []
    assert sorted(sends) == ["all@x.edu", "stripe@x.edu"]

    # Already published: a second cycle must not resend.
    pipeline.run_cycle()
    assert len(sends) == 2


# One canonical URL per posting: a shared one would make the reconcile pass
# fold all three into a single record before publish.
@patch("intake.pipeline.run_rules",
       side_effect=lambda p, http: GateResult(canonical_url=p.url, degree_levels=["BS"]))
def test_a_burst_of_postings_is_one_message_per_subscriber(_rules, tmp_path):
    store = Store(tmp_path / "b.db")
    confirmed(store, "all@x.edu", {})
    confirmed(store, "stripe@x.edu", build_filters(["Stripe Inc"]))
    calls = []
    pipeline = make_pipeline(
        tmp_path, store, lambda s, ps: calls.append((s["target"], len(ps))),
        detections=(DET, DET2, DET3),
    )

    report = pipeline.run_cycle()
    assert report.published == 3
    # Three postings, two subscribers, two messages. Per-posting fan-out
    # would have been five.
    assert sorted(calls) == [("all@x.edu", 3), ("stripe@x.edu", 2)]


@patch("intake.pipeline.run_rules",
       return_value=GateResult(canonical_url="https://stripe.com/jobs/1", degree_levels=["BS"]))
def test_sender_error_does_not_kill_the_cycle(_rules, tmp_path):
    store = Store(tmp_path / "t.db")
    confirmed(store, "all@x.edu", {})

    def boom(s, ps):
        raise RuntimeError("channel down")

    pipeline = make_pipeline(tmp_path, store, boom)
    report = pipeline.run_cycle()
    assert report.published == 1
    assert any(e.startswith("notify") for e in report.errors)
    assert pipeline.store.get(DET.dedupe_key()).status == Status.PUBLISHED


@patch("intake.pipeline.run_rules",
       return_value=GateResult(canonical_url="https://stripe.com/jobs/1", degree_levels=["BS"]))
def test_the_cycle_prunes_unconfirmed_subscriptions(_rules, tmp_path):
    store = Store(tmp_path / "pr.db")
    stale, _, _ = store.add_subscription("email", "stale@x.edu", {})
    store.conn.execute(
        "UPDATE subscriptions SET created_at = datetime('now', '-8 days') WHERE id = ?",
        (stale,))
    store.conn.commit()
    pipeline = make_pipeline(tmp_path, store, lambda s, ps: None)

    # verify=False is how production runs, so the prune has to happen on
    # that path too.
    pipeline.run_cycle(verify=False)
    assert store.active_subscriptions() == []
