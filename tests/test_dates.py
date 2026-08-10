from datetime import datetime, timedelta, timezone

from intake.dates import parse_epoch, parse_iso, parse_workday_relative


def test_parse_iso_variants():
    assert parse_iso("2026-08-08T11:57:57.969Z").year == 2026
    assert parse_iso("2026-08-08").day == 8
    assert parse_iso(None) is None
    assert parse_iso("Posted Today") is None


def test_parse_epoch_seconds_and_millis():
    assert parse_epoch(1754600000).year == 2025
    assert parse_epoch(1754600000000).year == 2025
    assert parse_epoch("garbage") is None


def test_workday_relative():
    now = datetime.now(timezone.utc)
    got = parse_workday_relative("Posted 3 Days Ago")
    assert abs((now - got) - timedelta(days=3)) < timedelta(minutes=1)
    assert parse_workday_relative("Posted Today").date() == now.date()
    assert parse_workday_relative("Posted 30+ Days Ago") is None
    assert parse_workday_relative(None) is None


def test_parse_season():
    from intake.dates import parse_season

    assert parse_season("Software Intern (Fall 2026)") == "Fall 2026"
    assert parse_season("SWE Intern - Summer '27") == "Summer 2027"
    assert parse_season("PhD Research Intern - 2026") == "2026"
    assert parse_season("Software Engineering Masters Internships") is None
