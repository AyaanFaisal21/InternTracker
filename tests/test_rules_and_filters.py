from datetime import datetime, timezone

from intake.detectors.base import looks_like_swe_internship
from intake.schema import Posting, Source, Status
from intake.verify.rules import check_title


def posting(title="Software Engineer Intern"):
    return Posting(
        id="abc", company="X", title=title, url="https://x.co",
        sources=[Source.GREENHOUSE],
        first_seen=datetime.now(timezone.utc), status=Status.PENDING,
    )


def test_prefilter_accepts_swe_intern_titles():
    assert looks_like_swe_internship("Software Engineer Intern")
    assert looks_like_swe_internship("Machine Learning Co-op")
    assert looks_like_swe_internship("2026 SWE Internship - Backend")


def test_prefilter_rejects_non_intern_and_non_swe():
    assert not looks_like_swe_internship("Senior Software Engineer")
    assert not looks_like_swe_internship("Culinary Intern")


def test_title_rule_rejects_disqualifiers():
    assert check_title(posting("Unpaid Software Intern")) is not None
    assert check_title(posting("Campus Brand Ambassador Intern")) is not None
    assert check_title(posting("Software Engineer Intern")) is None
