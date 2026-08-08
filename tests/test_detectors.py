"""Detector parsing tests against recorded fixtures. No network.

When an ATS changes its response format, refresh fixtures with
scripts/capture_fixtures.py and these tests show exactly what broke.
"""

from intake.detectors import AshbyDetector, GreenhouseDetector, LeverDetector
from intake.schema import Source

from .harness import fixture_client, load_fixture


def test_greenhouse_parses_and_prefilters():
    client = fixture_client({"boards-api.greenhouse.io": load_fixture("greenhouse_jobs.json")})
    dets = GreenhouseDetector(["stripe"], client=client).poll()
    # 3 jobs on the board; only the SWE intern passes the prefilter
    assert len(dets) == 1
    d = dets[0]
    assert d.source == Source.GREENHOUSE
    assert d.title == "Software Engineer, Intern"
    assert d.locations == ["New York, NY"]
    assert d.payload["gh_job_id"] == 100001


def test_lever_parses_and_prefilters():
    client = fixture_client({"api.lever.co": load_fixture("lever_postings.json")})
    dets = LeverDetector(["acme"], client=client).poll()
    assert len(dets) == 1
    assert dets[0].title.startswith("Software Engineer Intern")
    assert dets[0].locations == ["Palo Alto, CA"]


def test_ashby_parses_and_prefilters():
    client = fixture_client({"api.ashbyhq.com": load_fixture("ashby_board.json")})
    dets = AshbyDetector(["acme"], client=client).poll()
    assert len(dets) == 1
    assert dets[0].title == "Machine Learning Intern"
    assert dets[0].url == "https://jobs.ashbyhq.com/acme/ccc-333"


def test_bad_board_does_not_stop_sweep():
    # First board 404s (no fixture route); second succeeds.
    client = fixture_client({"boards/good/": load_fixture("greenhouse_jobs.json")})
    dets = GreenhouseDetector(["broken", "good"], client=client).poll()
    assert len(dets) == 1
