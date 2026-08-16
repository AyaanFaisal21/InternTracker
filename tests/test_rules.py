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


def test_program_ambassador_not_disqualified(tmp_path):
    p = make_posting(
        tmp_path, source=Source.OPPORTUNITY_LIST, company="GirlsWhoML",
        title="Thinking About Thinking 2026 Ambassador Programme",
        url="https://acme.com/prog", category="program",
    )
    client = fixture_client({"acme.com": "<p>ML mentorship for students</p>"})
    assert run_rules(p, client).reject_reason is None
