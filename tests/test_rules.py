"""run_rules against a fixture transport — unmocked, catches signature and
attribute drift that the pipeline tests (which patch run_rules) cannot."""

from intake.schema import RawDetection, Source
from intake.store import Store
from intake.verify.rules import GateResult, run_rules

from .harness import fixture_client


def make_posting(tmp_path, **kw):
    det = RawDetection(**kw)
    store = Store(tmp_path / "t.db")
    p, _ = store.upsert_detection(det)
    return p


def test_run_rules_full_pass(tmp_path):
    p = make_posting(
        tmp_path, source=Source.GREENHOUSE, company="Acme",
        title="Software Engineer Intern", url="https://acme.com/jobs/1",
    )
    client = fixture_client({"acme.com": "<p>Pursuing a Bachelor's degree, Fall 2026</p>"})
    g = run_rules(p, client)
    assert g.reject_reason is None
    assert g.canonical_url == "https://acme.com/jobs/1"
    assert g.degree_levels == ["BS"]
    assert g.season == "Fall 2026"  # page text fallback; title carries no season


def test_run_rules_workday_uses_cxs_detail(tmp_path):
    p = make_posting(
        tmp_path, source=Source.WORKDAY, company="NVIDIA",
        title="Software Engineering Intern",
        url="https://nvidia.wd5.myworkdayjobs.com/en-US/Site/job/X/Y_JR1",
    )
    client = fixture_client({
        "/en-US/Site/job/": "<html>js shell</html>",
        "/wday/cxs/nvidia/": {"jobPostingInfo": {"jobDescription": "PhD required"}},
    })
    g = run_rules(p, client)
    assert g.reject_reason is None and g.degree_levels == ["PhD"]


def test_run_rules_list_sourced_workday_url_uses_cxs_detail(tmp_path):
    # Snap R0046464 regression: the Workday link arrived via a list, not
    # the workday detector, so a source check never fetched detail text
    # and the PhD requirement was invisible to the classifier.
    p = make_posting(
        tmp_path, source=Source.GITHUB_LIST, company="Snap",
        title="Research Intern, User Modeling and Personalization",
        url=("https://wd1.myworkdaysite.com/recruiting/snapchat/snap/job/"
             "Bellevue-Washington/Research-Intern--User-Modeling-and-Personalization_R0046464-1"),
    )
    client = fixture_client({
        "myworkdaysite.com/recruiting/": "<html>js shell</html>",
        "/wday/cxs/snapchat/snap/": {"jobPostingInfo": {"jobDescription":
            "<p>Currently enrolled in a PhD program in a technical field</p>"}},
    })
    g = run_rules(p, client)
    assert g.reject_reason is None and g.degree_levels == ["PhD"]


def test_run_rules_flexible_season_stays_none(tmp_path):
    # a Summer/Fall title must not take the single season the page states
    p = make_posting(
        tmp_path, source=Source.GREENHOUSE, company="Acme",
        title="Software Engineer Intern (Summer/Fall 2027)",
        url="https://acme.com/jobs/3",
    )
    client = fixture_client(
        {"acme.com": "<p>The program runs Summer 2027. Pursuing a Bachelor's degree.</p>"}
    )
    g = run_rules(p, client)
    assert g.reject_reason is None and g.season is None


def test_run_rules_rejects_bad_title(tmp_path):
    p = make_posting(
        tmp_path, source=Source.GREENHOUSE, company="Acme",
        title="Unpaid Marketing Intern", url="https://acme.com/jobs/2",
    )
    g = run_rules(p, fixture_client({}))
    assert g.reject_reason is not None and g.canonical_url is None and g.degree_levels == []


def test_waf_block_keeps_posting(tmp_path):
    import httpx

    p = make_posting(
        tmp_path, source=Source.GITHUB_LIST, company="Tesla",
        title="Software Engineer Intern, AI Infrastructure",
        url="https://www.tesla.com/careers/search/job/1?utm_source=Simplify",
    )
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    g = run_rules(p, client)
    assert g.reject_reason is None
    assert g.canonical_url == "https://www.tesla.com/careers/search/job/1"


def test_hybrid_tech_title_passes_gate(tmp_path):
    p = make_posting(
        tmp_path, source=Source.GREENHOUSE, company="Cloudflare",
        title="AI Innovation Intern - Service Sales (Fall 2026)",
        url="https://acme.com/jobs/9",
    )
    client = fixture_client({"acme.com": "<p>details</p>"})
    assert run_rules(p, client).reject_reason is None  # verifier owns the gray zone


def test_pure_nontech_title_rejects(tmp_path):
    p = make_posting(
        tmp_path, source=Source.GREENHOUSE, company="Acme",
        title="Sales Development Intern", url="https://acme.com/jobs/10",
    )
    assert run_rules(p, fixture_client({})).reject_reason == "non-tech role"


def test_timeout_keeps_posting(tmp_path):
    import httpx

    def boom(request):
        raise httpx.ReadTimeout("slow site")

    p = make_posting(
        tmp_path, source=Source.GITHUB_LIST, company="Roblox",
        title="Software Engineer Intern", url="https://careers.roblox.com/jobs/1",
    )
    client = httpx.Client(transport=httpx.MockTransport(boom))
    g = run_rules(p, client)
    assert g.reject_reason is None
    assert g.canonical_url == "https://careers.roblox.com/jobs/1"


def test_run_rules_lever_apply_chrome_never_classifies(tmp_path):
    # Palantir regression: the lever /apply form page (school dropdown,
    # EEOC prose) is chrome, not a description. Degree tokens on it must
    # not classify when no qualifications excerpt exists.
    p = make_posting(
        tmp_path, source=Source.GITHUB_LIST, company="Palantir",
        title="Software Engineer Intern",
        url="https://jobs.lever.co/palantir/373eb939-6f57-4836-8479-be79a5e07249/apply",
    )
    client = fixture_client({
        "jobs.lever.co": "<div>School: McMaster University. Doctor of Philosophy fans club.</div>"
    })
    g = run_rules(p, client)
    assert g.reject_reason is None
    assert g.degree_levels == []


def test_run_rules_chrome_page_falls_back_to_quals(tmp_path):
    # poison outside the qualifications section, requirement inside it
    p = make_posting(
        tmp_path, source=Source.GITHUB_LIST, company="Palantir",
        title="Software Engineer Intern - Infrastructure",
        url="https://jobs.lever.co/palantir/f221738b-e97c-4ce3-a12a-17ada2b855e4/apply",
    )
    client = fixture_client({
        "jobs.lever.co": (
            "<p>Our team includes a Doctor of Philosophy.</p>"
            "<p>Qualifications: pursuing a Bachelor's degree in CS.</p>"
        )
    })
    g = run_rules(p, client)
    assert g.reject_reason is None
    assert g.degree_levels == ["BS"]
    assert "Bachelor" in (g.qualifications or "")


def test_run_rules_title_year_beats_bare_list_season(tmp_path, monkeypatch):
    # a list stored "Summer"; the title states the cycle. Title wins, and
    # the posting itself carries the resolution (the pipeline keeps a set
    # p.season, so GateResult alone could never repair it).
    monkeypatch.setattr("intake.dates._default_current_year", lambda: 2026)
    p = make_posting(
        tmp_path, source=Source.OPPORTUNITY_LIST, company="Acme",
        title="Software Engineer Intern (Summer 2026)", season="Summer",
        url="https://acme.com/jobs/20",
    )
    client = fixture_client({"acme.com": "<p>details</p>"})
    g = run_rules(p, client)
    assert g.reject_reason is None
    assert g.season == "Summer 2026"
    assert p.season == "Summer 2026"


def test_run_rules_fills_season_the_source_left_empty(tmp_path):
    # BAE "Summer Software Intern" / Terranox "Summer Intern" arrived with
    # season None; the title alone must fill it.
    p = make_posting(
        tmp_path, source=Source.GITHUB_LIST, company="BAE Systems",
        title="Summer Software Intern", url="https://acme.com/jobs/21",
    )
    client = fixture_client({"acme.com": "<p>details</p>"})
    g = run_rules(p, client)
    assert g.reject_reason is None
    assert g.season == "Summer"
    assert p.season == "Summer"


def test_run_rules_page_copyright_year_is_not_a_season(tmp_path, monkeypatch):
    # Keysight fingerprint: page-text fallback harvested "© 2000"
    monkeypatch.setattr("intake.dates._default_current_year", lambda: 2026)
    p = make_posting(
        tmp_path, source=Source.GITHUB_LIST, company="Keysight",
        title="Software Development Intern", url="https://acme.com/jobs/22",
    )
    client = fixture_client(
        {"acme.com": "<p>Pursuing a Bachelor's degree.</p><p>© 2000 Keysight Technologies</p>"}
    )
    g = run_rules(p, client)
    assert g.reject_reason is None
    assert g.season is None
    assert g.degree_levels == ["BS"]


def test_run_rules_clears_stale_stored_season(tmp_path, monkeypatch):
    monkeypatch.setattr("intake.dates._default_current_year", lambda: 2026)
    p = make_posting(
        tmp_path, source=Source.OPPORTUNITY_LIST, company="MLH",
        title="MLH Fellowship", season="Summer 2020",
        url="https://acme.com/jobs/23", category="program",
    )
    client = fixture_client({"acme.com": "<p>a remote software engineering fellowship</p>"})
    g = run_rules(p, client)
    assert g.reject_reason is None
    assert g.season is None
    assert p.season is None


def test_program_ambassador_not_disqualified(tmp_path):
    p = make_posting(
        tmp_path, source=Source.OPPORTUNITY_LIST, company="GirlsWhoML",
        title="Thinking About Thinking 2026 Ambassador Programme",
        url="https://acme.com/prog", category="program",
    )
    client = fixture_client({"acme.com": "<p>ML mentorship for students</p>"})
    assert run_rules(p, client).reject_reason is None
