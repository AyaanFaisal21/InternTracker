import httpx

from intake.normalize import resolve_canonical, strip_tracking


def test_strip_tracking_removes_utm_and_refs():
    url = "https://x.co/jobs/1?utm_source=Simplify&utm_medium=referral&ref=Simplify&gh_src=abc"
    assert strip_tracking(url) == "https://x.co/jobs/1"


def test_strip_tracking_keeps_functional_params():
    url = "https://jobs.ashbyhq.com/acme/apply?ashby_jid=123&utm_source=x"
    assert strip_tracking(url) == "https://jobs.ashbyhq.com/acme/apply?ashby_jid=123"


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_resolve_follows_redirect_to_employer_page():
    def handler(req):
        if "simplify.jobs" in str(req.url):
            return httpx.Response(302, headers={"location": "https://acme.com/careers/1?utm_source=Simplify"})
        return httpx.Response(200, text="ok")

    reason, canonical, text = resolve_canonical("https://simplify.jobs/p/abc", _client(handler))
    assert reason is None
    assert canonical == "https://acme.com/careers/1"
    assert text == "ok"


def test_resolve_rejects_dead_link():
    reason, canonical, text = resolve_canonical(
        "https://acme.com/gone", _client(lambda r: httpx.Response(404))
    )
    assert reason == "url returned 404" and canonical is None and text == ""


def test_unwrap_instagram_redirector():
    from intake.normalize import unwrap_redirector

    wrapped = ("https://l.instagram.com/?u=https%3A%2F%2Fstemgateway.nasa.gov%2Fs"
               "%2Fcourse-offering%2Fabc%3Ffbclid%3DXYZ&e=AUD123")
    assert unwrap_redirector(wrapped).startswith("https://stemgateway.nasa.gov/s/course-offering/abc")
    assert unwrap_redirector("https://acme.com/jobs/1") == "https://acme.com/jobs/1"
