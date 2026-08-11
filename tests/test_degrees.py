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


def test_strip_tags_removes_style_and_script_bodies():
    html = "<style>.x{-ms-flex:1}</style><script>var ms = 1;</script><p>BS required</p>"
    assert "ms-flex" not in strip_tags(html)
    assert "BS required" in strip_tags(html)


def test_css_vendor_prefix_not_ms_degree():
    assert classify("SWE Intern", "display:-ms-flexbox;-ms-flex-wrap:wrap") == []
