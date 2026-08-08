"""Detector parsing tests against recorded fixtures. No network.

When an ATS changes its response format, refresh fixtures with
scripts/capture_fixtures.py and these tests show exactly what broke.
"""

from intake.detectors import AshbyDetector, GreenhouseDetector, LeverDetector
from intake.schema import Source

from .harness import FIXTURES as FIXTURES_PATH, fixture_client, load_fixture


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


def test_workday_parses_and_builds_urls():
    from intake.config import WorkdayBoard

    from intake.detectors import WorkdayDetector

    client = fixture_client({"/wday/cxs/nvidia/": load_fixture("workday_jobs.json")})
    board = WorkdayBoard(
        company="NVIDIA", host="nvidia.wd5.myworkdayjobs.com",
        tenant="nvidia", site="NVIDIAExternalCareerSite",
    )
    dets = WorkdayDetector([board], client=client).poll()
    # manager role filtered out ("Solution Architect Manager" carries no SWE term)
    assert len(dets) == 1
    d = dets[0]
    assert d.source == Source.WORKDAY
    assert d.company == "NVIDIA"
    assert d.payload["req_id"] == "JR2022295"
    assert d.url == (
        "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"
        "/job/US-CA-Santa-Clara/SWE-Intern_JR2022295"
    )


def test_google_parses_ssr_links_and_dedupes():
    from intake.detectors.custom_sites import GoogleCareersDetector

    html = (FIXTURES_PATH / "google_results.html").read_text()
    client = fixture_client({"careers/applications/jobs/results": html})
    dets = GoogleCareersDetector(client=client).poll()
    titles = sorted(d.title for d in dets)
    # accountant filtered; duplicate id collapsed; student researcher counts as intern
    assert titles == [
        "Software Engineering Intern Summer 2027",
        "Student Researcher Bsms Fall 2026",
    ]
    assert all(d.url.startswith("https://www.google.com/about/careers") for d in dets)


def test_microsoft_parses_and_prefilters():
    from intake.detectors.custom_sites import MicrosoftDetector

    client = fixture_client({"gcsservices.careers.microsoft.com": load_fixture("microsoft_search.json")})
    dets = MicrosoftDetector(client=client).poll()
    assert len(dets) == 1
    assert dets[0].company == "Microsoft"
    assert dets[0].url == "https://jobs.careers.microsoft.com/global/en/job/1790000/"
