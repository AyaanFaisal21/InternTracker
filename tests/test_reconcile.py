"""Reconciliation of postings that minted different dedupe keys but resolved
to one canonical URL: two-tier identity (ATS job key, else fold-equal
titles), the merge policy, the grouped store query, collapsed-group
skipping, and the idempotency the per-cycle pipeline pass relies on.
Named cases come from the production dry run of 2026-08-16."""

from datetime import datetime, timezone

from intake.pipeline import (
    extract_job_key,
    fold_title,
    has_trailing_season,
    merge_duplicate,
    reconcile_duplicates,
)
from intake.schema import Posting, Source, Status, Verdict
from intake.store import Store

# Real production pair: both rows resolved to this exact Workday URL.
NVIDIA_CANON = (
    "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/"
    "US%2C-CA%2C-Santa-Clara/Software-Engineering-Intern--Dynamo---Fall-2026_JR2022295"
)
# A flattened board page: no per-req identifier anywhere in it.
CANON_NOKEY = "https://careers.example.com/university/open-roles"
T0 = datetime(2026, 8, 10, tzinfo=timezone.utc)
T1 = datetime(2026, 8, 12, tzinfo=timezone.utc)
T2 = datetime(2026, 8, 14, tzinfo=timezone.utc)


def posting(pid, title, first_seen, canonical_url=CANON_NOKEY, url=None,
            company="NVIDIA", status=Status.GATED, **kw):
    return Posting(
        id=pid, company=company, title=title, url=url or f"https://x.co/{pid}",
        canonical_url=canonical_url, first_seen=first_seen, status=status, **kw,
    )


def test_trailing_season_detection():
    assert has_trailing_season("Software Engineering Intern, Dynamo - Fall 2026")
    assert has_trailing_season("SWE Intern (Summer 2027)")
    assert has_trailing_season("SWE Intern Summer '26")
    assert has_trailing_season("SWE Intern - Winter")
    assert not has_trailing_season("Software Engineering Intern, Dynamo")
    assert not has_trailing_season("Fall 2026 Software Engineering Intern")
    assert not has_trailing_season("SWE Intern 2026 Cohort")


def test_extract_job_key():
    assert extract_job_key(NVIDIA_CANON) == ("nvidia.wd5.myworkdayjobs.com", "jr2022295")
    assert extract_job_key(
        "https://co.wd1.myworkdayjobs.com/ext/job/Loc/Title_R12345"
    ) == ("co.wd1.myworkdayjobs.com", "r12345")
    assert extract_job_key(
        "https://co.wd1.myworkdayjobs.com/ext/job/Loc/Title_1234567"
    )[1] == "1234567"
    assert extract_job_key(
        "https://boards.greenhouse.io/rippling/jobs/4567890"
    ) == ("boards.greenhouse.io", "4567890")
    assert extract_job_key(
        "https://jobs.lever.co/co/9f8a7b6c-1d2e-3f4a-5b6c-7d8e9f0a1b2c"
    ) == ("jobs.lever.co", "9f8a7b6c-1d2e-3f4a-5b6c-7d8e9f0a1b2c")
    assert extract_job_key(
        "https://jobs.ashbyhq.com/co/0aa11bb2-cc33-dd44-ee55-ff6677889900/application"
    )[1] == "0aa11bb2-cc33-dd44-ee55-ff6677889900"
    assert extract_job_key(CANON_NOKEY) is None
    assert extract_job_key("https://x.co/job/Intern_2026") is None  # year, not a req id
    assert extract_job_key(None) is None


def test_fold_title():
    assert fold_title("Software Engineer Intern") == fold_title(
        "Software Engineering Internship - Fall 2026"
    )
    assert fold_title("Software Engineer Intern/Co-op") == fold_title(
        "Software Engineer Intern"
    )
    assert fold_title("Software Engineer Intern - C++ or Python") != fold_title(
        "Software Engineer Intern"
    )
    assert fold_title("Frontend Software Engineer Intern") != fold_title(
        "Machine Learning Engineer Intern"
    )


def test_nvidia_dynamo_pair_suffixed_seen_first():
    # The live-board case: the suffixed title arrived first, the plain one
    # later. The earlier row survives but adopts the cleaner title.
    a = posting("aaa", "Software Engineering Intern, Dynamo - Fall 2026", T0,
                season="Fall 2026", sources=[Source.WORKDAY])
    b = posting("bbb", "Software Engineering Intern, Dynamo", T1,
                sources=[Source.GITHUB_LIST])
    merge_duplicate(a, b)
    assert a.title == "Software Engineering Intern, Dynamo"
    assert a.season == "Fall 2026"
    assert a.sources == [Source.WORKDAY, Source.GITHUB_LIST]
    assert b.status == Status.REJECTED
    assert b.reject_reason == "duplicate of aaa"


def test_nvidia_dynamo_pair_clean_seen_first():
    a = posting("aaa", "Software Engineering Intern, Dynamo", T0)
    b = posting("bbb", "Software Engineering Intern, Dynamo - Fall 2026", T1,
                season="Fall 2026")
    merge_duplicate(a, b)
    assert a.title == "Software Engineering Intern, Dynamo"
    assert a.season == "Fall 2026"  # carried from the duplicate
    assert b.status == Status.REJECTED


def test_both_titles_suffixed_keeps_survivors():
    a = posting("aaa", "SWE Intern - Fall 2026", T0)
    b = posting("bbb", "SWE Intern - Fall, 2026", T1)
    merge_duplicate(a, b)
    assert a.title == "SWE Intern - Fall 2026"


def test_union_carry_over_and_earliest_date():
    v = Verdict(is_swe_internship=True, is_open=True, is_legitimate=True,
                confidence="high", reasons=[])
    a = posting("aaa", "SWE Intern", T0, locations=["Santa Clara, CA"],
                sources=[Source.WORKDAY],
                date_posted=datetime(2026, 8, 9, tzinfo=timezone.utc))
    b = posting("bbb", "SWE Intern", T1, locations=["Santa Clara, CA", "Remote"],
                sources=[Source.GITHUB_LIST],
                date_posted=datetime(2026, 8, 7, tzinfo=timezone.utc),
                season="Fall 2026", degree_levels=["BS", "MS"],
                qualifications="C++ and CUDA", verdict=v)
    merge_duplicate(a, b)
    assert a.locations == ["Santa Clara, CA", "Remote"]
    assert a.sources == [Source.WORKDAY, Source.GITHUB_LIST]
    assert a.date_posted == datetime(2026, 8, 7, tzinfo=timezone.utc)
    assert a.season == "Fall 2026"
    assert a.degree_levels == ["BS", "MS"]
    assert a.qualifications == "C++ and CUDA"
    assert a.verdict is v


def test_survivor_fields_not_overwritten():
    a = posting("aaa", "SWE Intern", T0, season="Fall 2026", degree_levels=["BS"],
                qualifications="kept",
                date_posted=datetime(2026, 8, 5, tzinfo=timezone.utc))
    b = posting("bbb", "SWE Intern", T1, season="Spring 2027", degree_levels=["PhD"],
                qualifications="dropped",
                date_posted=datetime(2026, 8, 9, tzinfo=timezone.utc))
    merge_duplicate(a, b)
    assert a.season == "Fall 2026"
    assert a.degree_levels == ["BS"]
    assert a.qualifications == "kept"
    assert a.date_posted == datetime(2026, 8, 5, tzinfo=timezone.utc)


def test_duplicate_groups_ignores_unique_pending_and_rejected(tmp_path):
    store = Store(tmp_path / "t.db")
    store.update(posting("aaa", "SWE Intern", T0))
    store.update(posting("bbb", "SWE Intern - Fall 2026", T1))
    store.update(posting("ccc", "Other Intern", T0, canonical_url="https://x.co/other"))
    store.update(posting("ddd", "No URL Yet Intern", T0, canonical_url=None,
                         status=Status.PENDING))
    store.update(posting("eee", "SWE Intern (old)", T0, status=Status.REJECTED))
    groups = store.duplicate_groups()
    assert len(groups) == 1
    assert {p.id for p in groups[0]} == {"aaa", "bbb"}


def test_update_persists_title_change(tmp_path):
    # The reconcile pass is the first writer of a changed title; the upsert's
    # conflict clause must carry it (it did not, once).
    store = Store(tmp_path / "t.db")
    store.update(posting("aaa", "SWE Intern - Fall 2026", T0))
    p = store.get("aaa")
    p.title = "SWE Intern"
    store.update(p)
    assert store.get("aaa").title == "SWE Intern"


def test_tier1_nvidia_pair_real_urls(tmp_path):
    store = Store(tmp_path / "t.db")
    store.update(posting("aaa", "Software Engineering Intern, Dynamo - Fall 2026",
                         T0, canonical_url=NVIDIA_CANON, season="Fall 2026"))
    store.update(posting("bbb", "Software Engineering Intern, Dynamo", T1,
                         canonical_url=NVIDIA_CANON))
    assert reconcile_duplicates(store) == (1, 0)
    survivor = store.get("aaa")
    assert survivor.status == Status.GATED
    assert survivor.title == "Software Engineering Intern, Dynamo"
    assert store.get("bbb").status == Status.REJECTED
    assert store.get("bbb").reject_reason == "duplicate of aaa"


def test_tier1_merges_regardless_of_title(tmp_path):
    # A matching req id outranks any title distance.
    store = Store(tmp_path / "t.db")
    store.update(posting("aaa", "Software Engineer Intern, Fleet Tools", T0,
                         canonical_url=NVIDIA_CANON))
    store.update(posting("bbb", "SWE Interns 2026", T1, canonical_url=NVIDIA_CANON))
    assert reconcile_duplicates(store) == (1, 0)
    assert store.get("bbb").status == Status.REJECTED


def test_tier2_tower_identical_titles(tmp_path):
    store = Store(tmp_path / "t.db")
    store.update(posting("aaa", "Software Engineer Internship", T0, company="Tower"))
    store.update(posting("bbb", "Software Engineer Internship", T1, company="Tower"))
    assert reconcile_duplicates(store) == (1, 0)
    assert store.get("bbb").status == Status.REJECTED


def test_tier2_engineer_vs_engineering(tmp_path):
    # The HPR and PDT prod pairs: spelling noise only, keyless URL.
    store = Store(tmp_path / "t.db")
    store.update(posting("aaa", "Software Engineer Intern", T0))
    store.update(posting("bbb", "Software Engineering Intern", T1))
    assert reconcile_duplicates(store) == (1, 0)
    assert store.get("bbb").status == Status.REJECTED


def test_named_prod_pairs_do_not_merge(tmp_path):
    # Distinct roles flattened onto one keyless URL by gate redirects.
    pairs = [
        # SpaceX
        ("Software Engineer Intern/Co-op",
         "Software Engineer Intern, Fleet Health Instrumentation"),
        # Axiomatic AI
        ("Software Engineer Intern - AI/Agentic Systems",
         "Software Engineer Intern - Platform/DevOps"),
        # Veeam
        ("Software Engineer Intern - Platform, Security & AI",
         "Software Engineer Intern - Policy Engineering"),
        # Amgen: subset of tokens, still role-distinguishing
        ("Software Engineer Intern - Multiple Teams", "Software Engineer Intern"),
        # Hudson River Trading
        ("Software Engineer Intern - C++ or Python", "Software Engineer Intern"),
    ]
    for i, (t1, t2) in enumerate(pairs):
        store = Store(tmp_path / f"t{i}.db")
        store.update(posting("aaa", t1, T0))
        store.update(posting("bbb", t2, T1))
        assert reconcile_duplicates(store) == (0, 0), (t1, t2)
        assert store.get("bbb").status == Status.GATED, (t1, t2)


def test_rippling_trio_skips_as_collapsed(tmp_path):
    store = Store(tmp_path / "t.db")
    store.update(posting("aaa", "Frontend Software Engineer Intern", T0,
                         company="Rippling"))
    store.update(posting("bbb", "Machine Learning Engineer Intern", T1,
                         company="Rippling"))
    store.update(posting("ccc", "Software Engineer Intern", T2, company="Rippling"))
    seen = []
    merged, collapsed = reconcile_duplicates(
        store, on_collapse=lambda g: seen.append(len(g))
    )
    assert (merged, collapsed) == (0, 1)
    assert seen == [3]
    assert all(store.get(i).status == Status.GATED for i in ("aaa", "bbb", "ccc"))


def test_no_partial_tier2_merge_in_collapsed_group(tmp_path):
    # Two of three fold equal; a collapsed group still merges nothing weakly.
    store = Store(tmp_path / "t.db")
    store.update(posting("aaa", "Software Engineer Intern", T0))
    store.update(posting("bbb", "Software Engineering Internship", T1))
    store.update(posting("ccc", "3D Software Intern", T2))
    assert reconcile_duplicates(store) == (0, 1)
    assert all(store.get(i).status == Status.GATED for i in ("aaa", "bbb", "ccc"))


def test_rivian_trio_distinct_keys_skip_collapsed(tmp_path):
    # Distinct req ids in the detected urls prove distinct postings even
    # though the canonical flattened; counted as one collapsed group.
    store = Store(tmp_path / "t.db")
    rows = (
        ("aaa", "Software Engineer Intern, OTA Integration", T0, "1111111"),
        ("bbb", "Software Engineer Intern, 3D", T1, "2222222"),
        ("ccc", "Software Engineer Intern, Embedded Hardware", T2, "3333333"),
    )
    for pid, title, ts, gh in rows:
        store.update(posting(pid, title, ts, company="Rivian",
                             url=f"https://boards.greenhouse.io/rivian/jobs/{gh}"))
    assert reconcile_duplicates(store) == (0, 1)
    assert all(store.get(pid).status == Status.GATED for pid, _, _, _ in rows)


def test_tier1_merge_inside_collapsed_group(tmp_path):
    # Key matches still fold inside a group that otherwise stays collapsed.
    store = Store(tmp_path / "t.db")
    store.update(posting("aaa", "Software Engineer Intern, OTA", T0,
                         url="https://boards.greenhouse.io/x/jobs/9999999"))
    store.update(posting("bbb", "Software Engineering Internship, OTA", T1,
                         url="https://boards.greenhouse.io/x/jobs/9999999"))
    store.update(posting("ccc", "Machine Learning Intern", T2))
    assert reconcile_duplicates(store) == (1, 1)
    assert store.get("aaa").status == Status.GATED
    assert store.get("bbb").status == Status.REJECTED
    assert store.get("ccc").status == Status.GATED


def test_mixed_key_evidence_pair_does_not_merge(tmp_path):
    # Key on one side only blocks the weak tier even for fold-equal titles.
    store = Store(tmp_path / "t.db")
    store.update(posting("aaa", "Software Engineer Intern", T0,
                         url="https://boards.greenhouse.io/x/jobs/9999999"))
    store.update(posting("bbb", "Software Engineering Intern", T1))
    assert reconcile_duplicates(store) == (0, 0)
    assert store.get("bbb").status == Status.GATED


def test_survivor_is_first_sighting(tmp_path):
    store = Store(tmp_path / "t.db")
    store.update(posting("bbb", "SWE Intern - Fall 2026", T1))
    store.update(posting("aaa", "SWE Intern", T0))
    assert reconcile_duplicates(store) == (1, 0)
    assert store.get("aaa").status == Status.GATED
    dup = store.get("bbb")
    assert dup.status == Status.REJECTED
    assert dup.reject_reason == "duplicate of aaa"


def test_reconcile_is_idempotent(tmp_path):
    store = Store(tmp_path / "t.db")
    store.update(posting("aaa", "SWE Intern", T0))
    store.update(posting("bbb", "SWE Intern - Fall 2026", T1))
    assert reconcile_duplicates(store) == (1, 0)
    assert reconcile_duplicates(store) == (0, 0)


def test_dry_run_writes_nothing(tmp_path):
    store = Store(tmp_path / "t.db")
    store.update(posting("aaa", "SWE Intern", T0))
    store.update(posting("bbb", "SWE Intern - Fall 2026", T1))
    seen = []
    merged, collapsed = reconcile_duplicates(
        store, on_merge=lambda s, d: seen.append((s.id, d.id)), apply=False
    )
    assert (merged, collapsed) == (1, 0) and seen == [("aaa", "bbb")]
    assert store.get("bbb").status == Status.GATED
