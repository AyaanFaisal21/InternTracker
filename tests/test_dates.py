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

    # year pinned so the plausibility window stays [2025, 2029] forever
    assert parse_season("Software Intern (Fall 2026)", current_year=2026) == "Fall 2026"
    assert parse_season("SWE Intern - Summer '27", current_year=2026) == "Summer 2027"
    assert parse_season("PhD Research Intern - 2026", current_year=2026) == "2026"
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
    assert resolve_season(
        "SWE Intern - Fall 2026", "flexible start date", current_year=2026
    ) == "Fall 2026"
    assert resolve_season(
        "SWE Intern", "join us in Spring 2027", current_year=2026
    ) == "Spring 2027"
    assert resolve_season("SWE Intern", "start in fall, winter, or spring") is None


def test_season_year_window():
    from intake.dates import parse_season, season_year_plausible

    # window pinned to 2026: [2025, 2029]
    assert season_year_plausible(2025, current_year=2026)
    assert season_year_plausible(2029, current_year=2026)
    assert not season_year_plausible(2024, current_year=2026)
    assert not season_year_plausible(2030, current_year=2026)
    assert parse_season("SWE Intern Fall 2026", current_year=2026) == "Fall 2026"
    assert parse_season("SWE Intern 2029", current_year=2026) == "2029"


def test_stale_years_parse_to_none():
    from intake.dates import parse_season, resolve_season

    # copyright and founding years harvested from page text (Keysight
    # "© 2000", Shopify "2006", Palantir "2020", MLH "Summer 2020")
    assert parse_season("© 2000 Keysight Technologies", current_year=2026) is None
    assert parse_season("founded in 2006", current_year=2026) is None
    assert parse_season("Copyright 2020 Palantir", current_year=2026) is None
    assert parse_season("MLH Fellowship Summer 2020", current_year=2026) is None
    # page-text fallback cannot resurrect an out-of-window year
    assert resolve_season("SWE Intern", "© 2000 Keysight", current_year=2026) is None
    assert resolve_season(
        "Software Engineer Intern", "our Summer 2020 cohort was great",
        current_year=2026,
    ) is None


def test_reconcile_season_title_beats_bare_stored():
    from intake.dates import reconcile_season

    # list-sourced bare "Summer" vs a seasoned-year title: title wins
    assert reconcile_season(
        "Summer", "Product Summit (Summer 2026)", "", current_year=2026
    ) == "Summer 2026"
    assert reconcile_season(
        "Summer", "GSSoC 2026 - GirlScript Summer of Code", "", current_year=2026
    ) == "Summer 2026"
    # bare-year title joins the stored season word
    assert reconcile_season(
        "Summer", "Externship Program 2026", "", current_year=2026
    ) == "Summer 2026"
    # a seasoned stored value stands against the same title
    assert reconcile_season(
        "Summer 2026", "Externship Program 2026", "", current_year=2026
    ) == "Summer 2026"


def test_reconcile_season_fills_missing_from_title():
    from intake.dates import reconcile_season

    # BAE "Summer Software Intern" / Terranox "Summer Intern" stored None
    assert reconcile_season(None, "Summer Software Intern", "", current_year=2026) == "Summer"
    assert reconcile_season(
        None, "Summer Intern - AI/ML Engineering", "", current_year=2026
    ) == "Summer"


def test_reconcile_season_clears_stale_stored_years():
    from intake.dates import reconcile_season

    # stale stored year with nothing better: cleared
    assert reconcile_season("2000", "Software Development Intern", "", current_year=2026) is None
    assert reconcile_season("Summer 2020", "MLH Fellowship", "", current_year=2026) is None
    # stale stored year recomputes from the title when it can
    assert reconcile_season(
        "2020", "Software Engineer Intern (Fall 2026)", "", current_year=2026
    ) == "Fall 2026"
    # in-window stored years stand
    assert reconcile_season("2025", "AI Solutions Co-op", "", current_year=2026) == "2025"


def test_reconcile_season_keeps_flexible_semantics():
    from intake.dates import reconcile_season

    # flexible title parses to None: stored bare season survives, stored
    # None stays None
    assert reconcile_season(
        "Summer", "SWE Intern - Summer/Fall 2027", "", current_year=2026
    ) == "Summer"
    assert reconcile_season(None, "SWE Intern - Summer/Fall 2027", "", current_year=2026) is None
    # title-silent stored season survives untouched
    assert reconcile_season("Fall 2026", "Data Science Intern - R&D", "", current_year=2026) == "Fall 2026"
    assert reconcile_season("Spring", "Associate Software Engineer Intern", "", current_year=2026) == "Spring"
