from intake.degrees import classify, strip_tags, workday_detail_text

from .harness import fixture_client


def test_title_degree_is_decisive():
    assert classify("PhD Research Intern, Generative AI", "bachelor master phd") == ["PhD"]


def test_page_scan_finds_all_levels():
    text = "Currently pursuing a Bachelor's or Master's degree in CS or related field"
    assert classify("Software Engineer Intern", text) == ["BS", "MS"]


def test_no_mention_means_open():
    assert classify("Software Engineer Intern", "join our team this summer") == []


def test_abbreviations_match():
    assert classify("SWE Intern", "BS/MS in Computer Science required") == ["BS", "MS"]


def test_phd_program_phrasings():
    # Snap R0046464 wording
    text = ("Minimum Qualifications: Currently enrolled in a PhD program in a "
            "technical field such as computer science, machine learning, "
            "statistics, mathematics, or equivalent years of experience")
    assert classify("Research Intern, User Modeling and Personalization", text) == ["PhD"]
    assert classify("Research Intern", "currently pursuing a Ph.D. in machine learning") == ["PhD"]
    assert classify("Research Intern", "must be enrolled in a Ph. D. or D.Phil programme") == ["PhD"]


def test_masters_program_phrasing():
    assert classify("Research Intern", "currently enrolled in a Master's program in CS") == ["MS"]


def test_graph_data_is_not_phd():
    # bare ph\s?d without boundaries matched "graph data"
    assert classify("SWE Intern", "experience with graph data and graph databases") == []


def test_strip_tags():
    assert strip_tags("<p>BS <b>or</b> MS</p>").split() == ["BS", "or", "MS"]


def test_workday_detail_fetch():
    payload = {"jobPostingInfo": {"jobDescription": "<p>Pursuing a PhD in CS</p>"}}
    client = fixture_client({"/wday/cxs/nvidia/": payload})
    url = "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/X/Y_JR1"
    assert "PhD" in workday_detail_text(url, client)


def test_workday_detail_fetch_fails_soft():
    client = fixture_client({})  # every request 404s
    url = "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/X/Y_JR1"
    assert workday_detail_text(url, client) == ""


def test_workday_detail_fetch_no_locale():
    payload = {"jobPostingInfo": {"jobDescription": "<p>Pursuing a PhD in CS</p>"}}
    client = fixture_client({"/wday/cxs/nvidia/": payload})
    url = "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/X/Y_JR1"
    assert "PhD" in workday_detail_text(url, client)


def test_workday_detail_fetch_recruiting_url():
    # Snap R0046464: public URL is the wdN.myworkdaysite.com/recruiting
    # form; tenant and site sit in the path, CXS lives on the same host.
    payload = {"jobPostingInfo": {"jobDescription": "<p>Currently enrolled in a PhD program</p>"}}
    client = fixture_client({"wd1.myworkdaysite.com/wday/cxs/snapchat/snap/job/": payload})
    url = ("https://wd1.myworkdaysite.com/recruiting/snapchat/snap/job/"
           "Bellevue-Washington/Research-Intern--User-Modeling-and-Personalization_R0046464-1")
    text = workday_detail_text(url, client)
    assert classify("Research Intern, User Modeling and Personalization", text) == ["PhD"]


def test_non_workday_host_never_fetches():
    # a CXS route exists, but a non-Workday host must not reach it
    client = fixture_client({"/wday/cxs/": {"jobPostingInfo": {"jobDescription": "PhD"}}})
    assert workday_detail_text("https://example.com/en-US/site/job/x", client) == ""


def test_strip_tags_removes_style_and_script_bodies():
    html = "<style>.x{-ms-flex:1}</style><script>var ms = 1;</script><p>BS required</p>"
    assert "ms-flex" not in strip_tags(html)
    assert "BS required" in strip_tags(html)


def test_css_vendor_prefix_not_ms_degree():
    assert classify("SWE Intern", "display:-ms-flexbox;-ms-flex-wrap:wrap") == []


def test_eeoc_boilerplate_not_ms():
    text = ("Join our team this winter. "
            "Voluntary Self-Identification of Disability: conditions include "
            "Parkinson's disease, multiple sclerosis (MS), and others.")
    assert classify("Software Engineer Intern", text) == []


def test_abbrev_needs_degree_context():
    assert classify("SWE Intern", "our MS Teams channel is active") == []
    assert classify("SWE Intern", "pursuing a BS or MS degree in Computer Science") == ["BS", "MS"]
