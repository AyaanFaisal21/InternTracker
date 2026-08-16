"""Notification backbone: subscription store roundtrip, company matching,
and publish-time fan-out. Senders are capture callables; nothing leaves
the process."""

from unittest.mock import patch

from intake.config import Settings, Watchlist
from intake.notify import notify_new_posting
from intake.pipeline import Pipeline
from intake.schema import Posting, RawDetection, Source, Status, Verdict
from intake.store import Store
from intake.verify.rules import GateResult


def make_posting(company="Stripe"):
    return Posting.from_detection(RawDetection(
        source=Source.GREENHOUSE, company=company,
        title="Software Engineer Intern", url="https://x.co/1",
    ))


def sub(sid, companies=None):
    return {"id": sid, "channel": "email", "target": f"s{sid}@x.edu",
            "filters": {} if companies is None else {"companies": companies}}


# -- store roundtrip ------------------------------------------------------

def test_subscription_store_roundtrip(tmp_path):
    store = Store(tmp_path / "t.db")
    sid1, token1 = store.add_subscription("email", "a@b.edu", {"companies": ["NVIDIA"]})
    sid2, token2 = store.add_subscription("push", '{"endpoint": "https://p.example/x"}', {})
    assert sid1 != sid2 and token1 != token2 and len(token1) == 32

    subs = store.active_subscriptions()
    assert [s["id"] for s in subs] == [sid1, sid2]
    assert subs[0]["filters"] == {"companies": ["NVIDIA"]}  # decoded, not text
    assert subs[1]["filters"] == {}
    assert subs[0]["channel"] == "email" and subs[1]["target"].startswith("{")

    assert store.deactivate_by_token(token1) is True
    assert [s["id"] for s in store.active_subscriptions()] == [sid2]
    assert store.deactivate_by_token("not-a-token") is False


# -- matching -------------------------------------------------------------

def test_empty_filters_match_every_company():
    sends = []
    n = notify_new_posting([sub(1), sub(2)], make_posting("Anthropic"),
                           lambda s, p: sends.append(s["id"]))
    assert n == 2 and sends == [1, 2]


def test_company_filter_ignores_case_and_punctuation():
    sends = []
    subs = [sub(1, ["nvidia."]), sub(2, ["NVIDIA Corp"]), sub(3, [])]
    n = notify_new_posting(subs, make_posting("NVIDIA"),
                           lambda s, p: sends.append(s["id"]))
    # 1 matches via norm_text, 2 is a different normalized name and is
    # skipped, 3 declares no companies so it matches everything.
    assert n == 2 and sends == [1, 3]


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


def make_pipeline(tmp_path, store, sender):
    return Pipeline(
        settings=Settings(watchlist=Watchlist(), db_path=tmp_path / "t.db"),
        store=store,
        detectors=[FakeDetector([DET])],
        verifier=FakeVerifier(),
        publisher=lambda p: None,
        sender=sender,
    )


@patch("intake.pipeline.run_rules",
       return_value=GateResult(canonical_url="https://stripe.com/jobs/1", degree_levels=["BS"]))
def test_publish_fans_out_once_per_matching_subscription(_rules, tmp_path):
    store = Store(tmp_path / "t.db")
    store.add_subscription("email", "all@x.edu", {})
    store.add_subscription("email", "stripe@x.edu", {"companies": ["stripe!"]})
    store.add_subscription("email", "nv@x.edu", {"companies": ["NVIDIA"]})
    sends = []
    pipeline = make_pipeline(tmp_path, store, lambda s, p: sends.append(s["target"]))

    report = pipeline.run_cycle()
    assert report.published == 1 and report.errors == []
    assert sorted(sends) == ["all@x.edu", "stripe@x.edu"]

    # Already published: a second cycle must not resend.
    pipeline.run_cycle()
    assert len(sends) == 2


@patch("intake.pipeline.run_rules",
       return_value=GateResult(canonical_url="https://stripe.com/jobs/1", degree_levels=["BS"]))
def test_sender_error_does_not_kill_the_cycle(_rules, tmp_path):
    store = Store(tmp_path / "t.db")
    store.add_subscription("email", "all@x.edu", {})

    def boom(s, p):
        raise RuntimeError("channel down")

    pipeline = make_pipeline(tmp_path, store, boom)
    report = pipeline.run_cycle()
    assert report.published == 1
    assert any(e.startswith("notify") for e in report.errors)
    assert pipeline.store.get(DET.dedupe_key()).status == Status.PUBLISHED
