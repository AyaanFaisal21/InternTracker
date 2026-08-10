"""Suggestion intake: url validation flow and company board probe."""

from intake.detectors.suggestions import SuggestionDetector, slugify
from intake.schema import Source
from intake.store import Store

from .harness import fixture_client, load_fixture


def make_store(tmp_path):
    return Store(tmp_path / "t.db")


def test_url_suggestion_ingests_with_page_title(tmp_path):
    store = make_store(tmp_path)
    store.add_suggestion("url", "https://jobs.acme.dev/roles/42", company="Acme")
    client = fixture_client({
        "jobs.acme.dev": "<html><title>Quant Intern - Acme Careers</title></html>",
    })
    dets = SuggestionDetector(store, client=client).poll()
    assert len(dets) == 1
    assert dets[0].source == Source.SUGGESTION
    assert dets[0].title == "Quant Intern - Acme Careers"
    sug = store.recent_suggestions()[0]
    assert sug["status"] == "matched"


def test_dead_url_suggestion_no_match(tmp_path):
    store = make_store(tmp_path)
    store.add_suggestion("url", "https://gone.example.com/x")
    dets = SuggestionDetector(store, client=fixture_client({})).poll()
    assert dets == []
    assert store.recent_suggestions()[0]["status"] == "no_match"


def test_company_probe_finds_greenhouse_board(tmp_path):
    store = make_store(tmp_path)
    store.add_suggestion("company", "Stripe")
    client = fixture_client({"boards-api.greenhouse.io/v1/boards/stripe/": load_fixture("greenhouse_jobs.json")})
    dets = SuggestionDetector(store, client=client).poll()
    assert len(dets) == 1  # SWE intern from the fixture board
    sug = store.recent_suggestions()[0]
    assert sug["status"] == "matched"
    assert "greenhouse" in sug["result"]


def test_company_probe_keyword_filter(tmp_path):
    store = make_store(tmp_path)
    store.add_suggestion("company", "Stripe", keywords="culinary")
    client = fixture_client({"boards-api.greenhouse.io/v1/boards/stripe/": load_fixture("greenhouse_jobs.json")})
    dets = SuggestionDetector(store, client=client).poll()
    assert len(dets) == 1
    assert dets[0].title == "Culinary Intern"  # keyword overrides SWE prefilter


def test_company_probe_no_board(tmp_path):
    store = make_store(tmp_path)
    store.add_suggestion("company", "Totally Unknown Co")
    dets = SuggestionDetector(store, client=fixture_client({})).poll()
    assert dets == []
    assert store.recent_suggestions()[0]["status"] == "no_match"


def test_slugify_variants():
    got = slugify("Anduril Industries")
    assert "andurilindustries" in got and "anduril" in got


def test_keyword_intern_does_not_match_international():
    from intake.detectors.suggestions import _kw_match

    assert not _kw_match("intern", "international deployment manager")
    assert _kw_match("intern", "software engineer intern")
    assert _kw_match("intern", "software engineering internships")
    assert _kw_match("fpga", "fpga engineer")
    assert not _kw_match("fpga", "afpgab nonsense")
