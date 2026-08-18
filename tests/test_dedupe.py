from intake.schema import RawDetection, Source
from intake.store import Store


def det(source=Source.GREENHOUSE, company="Stripe", title="Software Engineer Intern",
        url="https://x.co/1", locations=None):
    return RawDetection(
        source=source, company=company, title=title, url=url,
        locations=locations or ["NYC"],
    )


def test_same_posting_from_two_sources_merges(tmp_path):
    store = Store(tmp_path / "t.db")
    p1, new1 = store.upsert_detection(det(source=Source.GREENHOUSE))
    p2, new2 = store.upsert_detection(det(source=Source.GITHUB_LIST, url="https://other.co/2"))
    assert new1 and not new2
    assert p1.id == p2.id
    assert set(p2.sources) == {Source.GREENHOUSE, Source.GITHUB_LIST}


def test_dedupe_key_ignores_case_and_punctuation():
    a = det(company="Stripe", title="Software Engineer Intern")
    b = det(company="STRIPE", title="software engineer, intern!")
    assert a.dedupe_key() == b.dedupe_key()


def test_different_titles_do_not_merge(tmp_path):
    store = Store(tmp_path / "t.db")
    _, new1 = store.upsert_detection(det(title="SWE Intern"))
    _, new2 = store.upsert_detection(det(title="ML Intern"))
    assert new1 and new2


def test_repoll_is_idempotent(tmp_path):
    store = Store(tmp_path / "t.db")
    store.upsert_detection(det())
    _, is_new = store.upsert_detection(det())
    assert not is_new


def test_unchanged_repoll_returns_no_row(tmp_path):
    # The Postgres backend cannot produce a row without a metered read, so a
    # no-op merge returns None on both backends. Callers must not assume one.
    store = Store(tmp_path / "t.db")
    store.upsert_detection(det())
    posting, is_new = store.upsert_detection(det())
    assert posting is None and not is_new


def test_embedded_greenhouse_id_is_a_job_key():
    """Companies hosting a Greenhouse board on their own site put the req id
    in the query, not the path. Missing it split one Datadog job into two."""
    from intake.pipeline import extract_job_key

    a = "https://careers.datadoghq.com/detail/8052095/?gh_jid=8052095"
    b = "https://careers.datadoghq.com/detail/8052095/?gh_jid=8052095&gh_src=x"
    assert extract_job_key(a) == ("careers.datadoghq.com", "8052095")
    assert extract_job_key(a) == extract_job_key(b)   # tracking params are noise
    assert extract_job_key("https://careers.datadoghq.com/detail/8052095/") is None
