"""run_rules against a fixture transport — unmocked, catches signature and
attribute drift that the pipeline tests (which patch run_rules) cannot."""

from intake.schema import RawDetection, Source
from intake.store import Store
from intake.verify.rules import run_rules

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
    client = fixture_client({"acme.com": "<p>Pursuing a Bachelor's degree</p>"})
    reason, canonical, degrees, quals = run_rules(p, client)
    assert reason is None
    assert canonical == "https://acme.com/jobs/1"
    assert degrees == ["BS"]


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
    reason, canonical, degrees, quals = run_rules(p, client)
    assert reason is None and degrees == ["PhD"]


def test_run_rules_rejects_bad_title(tmp_path):
    p = make_posting(
        tmp_path, source=Source.GREENHOUSE, company="Acme",
        title="Unpaid Marketing Intern", url="https://acme.com/jobs/2",
    )
    reason, canonical, degrees, quals = run_rules(p, fixture_client({}))
    assert reason is not None and canonical is None and degrees == []
