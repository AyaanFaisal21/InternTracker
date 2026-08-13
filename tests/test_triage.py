from intake.triage import audience_tags, refine_category


def test_event_urls_promote():
    assert refine_category("internship", "NVIDIA Candidate Event",
                           "https://nvidia.eightfold.ai/events/candidate/landing?x=1") == "event"
    assert refine_category("internship", "Roblox Rising Builders 2027",
                           "https://2027robloxrisingbuilders.splashthat.com/") == "event"
    assert refine_category("program", "NASA Pathways Fall Virtual Event",
                           "https://stemgateway.nasa.gov/s/course-offering/a0B") == "event"


def test_work_postings_stay():
    assert refine_category("internship", "Software Engineer Intern",
                           "https://boards.greenhouse.io/x/jobs/1") == "internship"
    assert refine_category("scholarship", "Future Engineers Event Scholarship",
                           "https://x.co/events/1") == "scholarship"


def test_audience_tags():
    assert audience_tags("Microsoft Explore for Freshmen and Sophomores") == ["underclassmen"]
    assert audience_tags("Grace Hopper Women in Tech Summit") == ["diversity"]
    assert audience_tags("First-Year Hispanic Scholars Program") == ["underclassmen", "diversity"]
    assert audience_tags("Software Engineer Intern") == []
