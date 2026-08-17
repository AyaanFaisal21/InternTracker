"""Suggestion intake: url flow, company board probe, and the resolver tier.

resolver=None everywhere the probe is under test: it means "run without a
resolver", so these stay probe-only regardless of whether the developer's
environment happens to hold an API key.
"""

from intake.config import Settings, Watchlist
from intake.detectors.suggestions import SuggestionDetector, slugify
from intake.resolve import CompanyResolution, GuardedResolver, ResolvedPosting
from intake.schema import Source
from intake.store import Store

from .harness import fixture_client, load_fixture


def make_store(tmp_path):
    return Store(tmp_path / "t.db")


def settings(**over):
    return Settings(watchlist=Watchlist(), **over)


class FakeResolver:
    """Inner resolver for the guard. Counts calls so a probe hit that reaches
    it fails loudly."""

    def __init__(self, result=None, boom=None):
        self.result = result or CompanyResolution()
        self.boom = boom
        self.calls = 0

    def resolve(self, company, keywords=None):
        self.calls += 1
        if self.boom:
            raise self.boom
        return self.result


GS = CompanyResolution(
    careers_url="https://www.goldmansachs.com/careers/students",
    ats_family="custom",
    postings=[
        ResolvedPosting(title="2027 Summer Analyst - Engineering",
                        url="https://higher.gs.com/roles/1"),
        ResolvedPosting(title="Software Engineering Intern",
                        url="https://higher.gs.com/roles/2"),
    ],
    confidence="high",
)


def guarded(store, inner, **over):
    return GuardedResolver(store, inner, settings(**over))


def test_url_suggestion_ingests_with_page_title(tmp_path):
    store = make_store(tmp_path)
    store.add_suggestion("url", "https://jobs.acme.dev/roles/42", company="Acme")
    client = fixture_client({
        "jobs.acme.dev": "<html><title>Quant Intern - Acme Careers</title></html>",
    })
    dets = SuggestionDetector(store, client=client, resolver=None).poll()
    assert len(dets) == 1
    assert dets[0].source == Source.SUGGESTION
    assert dets[0].title == "Quant Intern - Acme Careers"
    sug = store.recent_suggestions()[0]
    assert sug["status"] == "matched"


def test_dead_url_suggestion_no_match(tmp_path):
    store = make_store(tmp_path)
    store.add_suggestion("url", "https://gone.example.com/x")
    dets = SuggestionDetector(store, client=fixture_client({}), resolver=None).poll()
    assert dets == []
    assert store.recent_suggestions()[0]["status"] == "no_match"


def test_company_probe_finds_greenhouse_board(tmp_path):
    store = make_store(tmp_path)
    store.add_suggestion("company", "Stripe")
    client = fixture_client({"boards-api.greenhouse.io/v1/boards/stripe/": load_fixture("greenhouse_jobs.json")})
    dets = SuggestionDetector(store, client=client, resolver=None).poll()
    assert len(dets) == 1  # SWE intern from the fixture board
    sug = store.recent_suggestions()[0]
    assert sug["status"] == "matched"
    assert "greenhouse" in sug["result"]


def test_company_probe_keyword_filter(tmp_path):
    store = make_store(tmp_path)
    store.add_suggestion("company", "Stripe", keywords="culinary")
    client = fixture_client({"boards-api.greenhouse.io/v1/boards/stripe/": load_fixture("greenhouse_jobs.json")})
    dets = SuggestionDetector(store, client=client, resolver=None).poll()
    assert len(dets) == 1
    assert dets[0].title == "Culinary Intern"  # keyword overrides SWE prefilter


def test_probe_hit_never_reaches_the_resolver(tmp_path):
    store = make_store(tmp_path)
    store.add_suggestion("company", "Stripe")
    client = fixture_client({"boards-api.greenhouse.io/v1/boards/stripe/": load_fixture("greenhouse_jobs.json")})
    inner = FakeResolver(result=GS)
    dets = SuggestionDetector(store, client=client, resolver=guarded(store, inner)).poll()
    assert len(dets) == 1
    assert inner.calls == 0  # the cheap tier is right for the companies it covers
    assert store.resolver_calls_today() == 0


def test_company_probe_no_board_without_resolver(tmp_path):
    store = make_store(tmp_path)
    store.add_suggestion("company", "Totally Unknown Co")
    dets = SuggestionDetector(store, client=fixture_client({}), resolver=None).poll()
    assert dets == []
    sug = store.recent_suggestions()[0]
    assert sug["status"] == "no_match"
    # The old wording read as a verdict on the company. This one describes
    # our coverage and says the suggestion is still alive.
    assert "queued for review" in sug["result"]
    assert "deep search is off" in sug["result"]


def test_probe_miss_emits_resolver_postings(tmp_path):
    store = make_store(tmp_path)
    store.add_suggestion("company", "Goldman Sachs")
    inner = FakeResolver(result=GS)
    dets = SuggestionDetector(
        store, client=fixture_client({}), resolver=guarded(store, inner)
    ).poll()

    assert inner.calls == 1
    assert len(dets) == 2
    assert {d.source for d in dets} == {Source.SUGGESTION}
    # The title the coarse SWE prefilter would have dropped is exactly the
    # one this tier exists to catch.
    assert "2027 Summer Analyst - Engineering" in [d.title for d in dets]
    assert dets[0].payload["careers_url"] == GS.careers_url
    sug = store.recent_suggestions()[0]
    assert sug["status"] == "matched" and "2 posting(s)" in sug["result"]


def test_resolver_keywords_still_filter(tmp_path):
    store = make_store(tmp_path)
    store.add_suggestion("company", "Goldman Sachs", keywords="software")
    dets = SuggestionDetector(
        store, client=fixture_client({}), resolver=guarded(store, FakeResolver(result=GS))
    ).poll()
    assert [d.title for d in dets] == ["Software Engineering Intern"]


def test_resolver_board_hit_reports_the_watchlist_slug(tmp_path):
    store = make_store(tmp_path)
    store.add_suggestion("company", "Anduril Industries")
    found = CompanyResolution(
        careers_url="https://www.anduril.com/careers",
        ats_family="greenhouse", ats_slug="andurilindustries2", confidence="high",
    )
    SuggestionDetector(
        store, client=fixture_client({}), resolver=guarded(store, FakeResolver(result=found))
    ).poll()
    sug = store.recent_suggestions()[0]
    assert sug["status"] == "matched"
    assert "add to watchlist" in sug["result"] and "andurilindustries2" in sug["result"]


def test_resolver_finds_nothing_says_so_honestly(tmp_path):
    store = make_store(tmp_path)
    store.add_suggestion("company", "Nowhere Inc")
    dets = SuggestionDetector(
        store, client=fixture_client({}), resolver=guarded(store, FakeResolver())
    ).poll()
    assert dets == []
    sug = store.recent_suggestions()[0]
    assert sug["status"] == "no_match"
    assert sug["result"].startswith("no public postings found yet; queued for review")


def test_garbage_company_row_never_reaches_a_paid_call(tmp_path):
    # Queued before the endpoint validated anything; the detector re-checks.
    store = make_store(tmp_path)
    store.add_suggestion("company", "99999")
    inner = FakeResolver(result=GS)
    dets = SuggestionDetector(
        store, client=fixture_client({}), resolver=guarded(store, inner)
    ).poll()
    assert dets == [] and inner.calls == 0
    assert store.recent_suggestions()[0]["status"] == "no_match"
    assert store.resolver_calls_today() == 0


def test_resolver_exception_is_contained(tmp_path):
    store = make_store(tmp_path)
    store.add_suggestion("company", "Goldman Sachs")
    store.add_suggestion("url", "https://jobs.acme.dev/roles/42", company="Acme")
    client = fixture_client({
        "jobs.acme.dev": "<html><title>SWE Intern - Acme</title></html>",
    })
    inner = FakeResolver(boom=TimeoutError("upstream slow"))
    dets = SuggestionDetector(store, client=client, resolver=guarded(store, inner)).poll()

    # The cycle keeps going: the url suggestion behind the failure still runs.
    assert [d.company for d in dets] == ["Acme"]
    by_value = {s["value"]: s for s in store.recent_suggestions()}
    failed = by_value["Goldman Sachs"]
    assert failed["status"] == "error" and "TimeoutError" in failed["result"]


def test_second_submission_of_a_cached_company_costs_nothing(tmp_path):
    store = make_store(tmp_path)
    inner = FakeResolver(result=GS)
    resolver = guarded(store, inner)
    for value in ("Goldman Sachs", "goldman sachs."):
        store.add_suggestion("company", value)
        SuggestionDetector(store, client=fixture_client({}), resolver=resolver).poll()
    assert inner.calls == 1
    assert store.resolver_calls_today() == 1
    assert "[cached]" in store.recent_suggestions()[0]["result"]


def test_per_cycle_cap_leaves_the_rest_queued(tmp_path):
    store = make_store(tmp_path)
    for name in ("Alpha Bank", "Beta Bank", "Gamma Bank"):
        store.add_suggestion("company", name)
    inner = FakeResolver(result=GS)
    detector = SuggestionDetector(
        store, client=fixture_client({}), resolver=guarded(store, inner, resolver_per_cycle=2)
    )

    detector.poll()
    assert inner.calls == 2
    held = [s for s in store.recent_suggestions() if s["status"] == "new"]
    assert len(held) == 1 and "next cycle" in held[0]["result"]

    detector.poll()  # next cycle picks the held row up
    assert inner.calls == 3
    assert [s["status"] for s in store.recent_suggestions()] == ["matched"] * 3


def test_daily_budget_holds_suggestions_for_tomorrow(tmp_path):
    store = make_store(tmp_path)
    store.add_suggestion("company", "Alpha Bank")
    store.add_suggestion("company", "Beta Bank")
    inner = FakeResolver(result=GS)
    SuggestionDetector(
        store, client=fixture_client({}),
        resolver=guarded(store, inner, resolver_daily_budget=1),
    ).poll()

    assert inner.calls == 1
    held = [s for s in store.recent_suggestions() if s["status"] == "new"]
    assert len(held) == 1 and "daily research budget" in held[0]["result"]


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
