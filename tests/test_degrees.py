from intake.degrees import classify, classify_posting, strip_tags, workday_detail_text

from .harness import fixture_client

# Lever /apply form chrome: school dropdown plus EEOC self-identification
# prose, faithful to the Palantir pages that tagged 12 prod rows MS.
PALANTIR_CHROME = (
    "Submit your application. Resume. School Select a school "
    "McMaster University McGill University Rutgers University Other. "
    "Voluntary Self-Identification of Disability. Disabilities include "
    "Parkinson's disease and multiple sclerosis (MS). "
    "Equal Employment Opportunity is the law."
)

# Google careers error shell: the job is gone, the page fills with
# related-job links that name other jobs' degree levels.
GOOGLE_404 = (
    "Job not found. The job you are looking for may have been removed. "
    "Explore similar jobs: Software Engineering Intern, PhD, Summer 2027. "
    "Student Researcher, MS, Winter 2026. Sign in. Privacy."
)


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


def test_mcmaster_is_not_a_masters_degree():
    # the unbounded "master" substring matched the school dropdown
    assert classify("Software Engineer Intern", "McMaster University") == []
    assert classify("SWE Intern", "webmaster and scrummaster tooling") == []
    # the bounded forms still match
    assert classify("SWE Intern", "pursuing a Master's degree in CS") == ["MS"]
    assert classify("SWE Intern", "Masters students welcome") == ["MS"]


def test_msc_phrasings_match():
    # Rippling: "M.Sc. or Ph.D. program" missed MS entirely
    assert classify("Research Intern", "enrolled in an M.Sc. or Ph.D. program") == ["MS", "PhD"]
    assert classify("Research Intern", "MSc in Computer Science required") == ["MS"]


def test_postdoctoral_is_not_doctoral():
    assert classify("Research Fellow", "our postdoctoral community") == []


def test_palantir_chrome_text_never_classifies():
    # pattern level: bounded master + EEOC boilerplate cut
    assert classify("Forward Deployed Software Engineer Intern", PALANTIR_CHROME) == []
    # provenance level: a lever /apply URL disqualifies page text outright
    assert classify_posting(
        "Forward Deployed Software Engineer Intern",
        PALANTIR_CHROME,
        None,
        "https://jobs.lever.co/palantir/d582cd84-14fd-4aa3-b413-15982d286bd9/apply",
    ) == []


def test_error_page_never_classifies():
    # the raw text is poisoned by related-job links...
    assert "PhD" in classify("Software Engineering Intern", GOOGLE_404)
    # ...but an error page must not feed classification at all
    assert classify_posting(
        "Software Engineering Intern",
        GOOGLE_404,
        None,
        "https://www.google.com/about/careers/applications/jobs/results/8556-x",
    ) == []


def test_chrome_urls_fall_back_to_qualifications():
    quals = "Qualifications: pursuing a Bachelor's degree in Computer Science"
    # greenhouse embed shell, no quals: unstated
    assert classify_posting(
        "Campus AI Researcher Intern",
        "nav nav PhD sibling tile",
        None,
        "https://job-boards.greenhouse.io/embed/job_app?for=jumptrading&token=7976964",
    ) == []
    # icims mobile shell with a stored excerpt: the excerpt speaks
    assert classify_posting(
        "Agentic AI Research Intern",
        "chrome chrome Doctor of Philosophy chrome",
        quals,
        "https://careers-cotiviti.icims.com/jobs/19480/job?mobile=true&needsRedirect=false",
    ) == ["BS"]


def test_title_stays_decisive_on_chrome_urls():
    assert classify_posting(
        "Software Undergrad Internships",
        PALANTIR_CHROME,
        None,
        "https://jobs.lever.co/x/y/apply",
    ) == ["BS"]


def test_qualifications_outrank_page_text():
    # Haize Labs fingerprint: founder bio in page prose ("MIT PhD with
    # 21,000+ citations") while the requirements ask for a bachelor's
    page = (
        "Our founders: an MIT PhD with 21,000+ citations. "
        "Qualifications: pursuing a Bachelor's degree in CS."
    )
    quals = "Qualifications: pursuing a Bachelor's degree in CS."
    assert classify_posting("Software Engineer Intern", page, quals,
                            "https://job-boards.greenhouse.io/haizelabs/jobs/1") == ["BS"]
    # without a quals excerpt the page text still speaks (nothing better)
    assert classify_posting("Software Engineer Intern", page, None,
                            "https://job-boards.greenhouse.io/haizelabs/jobs/1") == ["BS", "PhD"]
