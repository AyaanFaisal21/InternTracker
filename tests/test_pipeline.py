"""Pipeline test with all external surfaces faked: detectors, rule HTTP,
verifier agent, publisher. Verifies status transitions and the publish gate."""

from datetime import datetime, timezone
from unittest.mock import patch

from intake.config import Settings, Watchlist
from intake.pipeline import Pipeline
from intake.schema import RawDetection, Source, Status, Verdict
from intake.store import Store


class FakeDetector:
    name = "fake"

    def __init__(self, detections):
        self.detections = detections

    def poll(self):
        return self.detections


class FakeVerifier:
    def __init__(self, approved=True):
        self.approved = approved

    def verify(self, p):
        return Verdict(
            is_swe_internship=self.approved, is_open=True, is_legitimate=True,
            season="Summer 2026", degree_levels=["BS", "MS"], confidence="high",
            reasons=[] if self.approved else ["not a SWE role"],
        )


def make_pipeline(tmp_path, detections, approved=True):
    published = []
    pipeline = Pipeline(
        settings=Settings(watchlist=Watchlist(), db_path=tmp_path / "t.db"),
        store=Store(tmp_path / "t.db"),
        detectors=[FakeDetector(detections)],
        verifier=FakeVerifier(approved),
        publisher=published.append,
    )
    return pipeline, published


DET = RawDetection(
    source=Source.GREENHOUSE, company="Stripe",
    title="Software Engineer Intern", url="https://stripe.com/jobs/1",
)


@patch("intake.pipeline.run_rules", return_value=(None, "https://stripe.com/jobs/1", ["BS"], None))
def test_approved_posting_is_published(_rules, tmp_path):
    pipeline, published = make_pipeline(tmp_path, [DET], approved=True)
    report = pipeline.run_cycle()
    assert report.new == 1 and report.verified == 1 and report.published == 1
    assert len(published) == 1
    assert pipeline.store.get(DET.dedupe_key()).status == Status.PUBLISHED


@patch("intake.pipeline.run_rules", return_value=(None, "https://stripe.com/jobs/1", ["BS"], None))
def test_rejected_posting_never_publishes(_rules, tmp_path):
    pipeline, published = make_pipeline(tmp_path, [DET], approved=False)
    report = pipeline.run_cycle()
    assert report.agent_rejected == 1 and report.published == 0
    assert published == []
    p = pipeline.store.get(DET.dedupe_key())
    assert p.status == Status.REJECTED and p.verdict is not None


@patch("intake.pipeline.run_rules", return_value=("url returned 404", None, [], None))
def test_rule_rejection_skips_agent(_rules, tmp_path):
    pipeline, published = make_pipeline(tmp_path, [DET])
    report = pipeline.run_cycle()
    assert report.rule_rejected == 1 and report.verified == 0
    assert pipeline.store.get(DET.dedupe_key()).reject_reason == "url returned 404"


@patch("intake.pipeline.run_rules", return_value=(None, "https://stripe.com/jobs/1", ["BS"], None))
def test_no_verify_parks_at_gated(_rules, tmp_path):
    pipeline, published = make_pipeline(tmp_path, [DET])
    report = pipeline.run_cycle(verify=False)
    assert report.new == 1 and report.verified == 0 and published == []
    p = pipeline.store.get(DET.dedupe_key())
    assert p.status == Status.GATED
    assert p.canonical_url == "https://stripe.com/jobs/1"
    assert p.degree_levels == ["BS"]
