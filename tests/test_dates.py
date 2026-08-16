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


def test_multi_season_parses_to_none():
    from intake.dates import parse_season

    # a single label would hide these from the other seasons' filters
    assert parse_season("SWE Intern - Summer/Fall 2027") is None
    assert parse_season("Software Intern (Fall or Winter 2026)") is None
    assert parse_season("Intern - Winter, Spring, and Summer") is None
    assert parse_season("Co-op, Fall 2026 / Spring 2027") is None


def test_flexible_phrasings_detected():
    from intake.dates import season_is_flexible

    assert season_is_flexible("flexible start date")
    assert season_is_flexible("interns join year-round")
    assert season_is_flexible("we hire on an ongoing basis")
    assert season_is_flexible("multiple start dates available")
    assert season_is_flexible("open to all seasons")
    assert not season_is_flexible("Software Intern (Fall 2026)")


def test_resolve_season_flexible_stops_chain():
    from intake.dates import resolve_season

    # flexible title must not pick a single season out of the page text
    assert resolve_season("SWE Intern - Summer/Fall 2027", "starts Summer 2027") is None
    # single-season title stays decisive over flexible page text
    assert resolve_season("SWE Intern - Fall 2026", "flexible start date") == "Fall 2026"
    assert resolve_season("SWE Intern", "join us in Spring 2027") == "Spring 2027"
    assert resolve_season("SWE Intern", "start in fall, winter, or spring") is None
