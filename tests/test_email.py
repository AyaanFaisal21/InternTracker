"""Email delivery over SES: the exact request shape, the compliance
elements every message must carry, the subject lines, and the failure paths.

FakeSES stands in for the boto3 SESv2 client and records every call, so
these tests assert the contract AWS would see without a network, a
credential, or boto3 itself being installed. Nothing here can send mail:
the client is injected, and the one test that exercises the real
construction path forces the import to fail.
"""

import email
import logging

import pytest

from intake.config import NotifySettings
from intake.notify import LogSender
from intake.schema import Posting, RawDetection, Source
from intake.senders import (
    EmailSender,
    alert_subject,
    build_sender,
    clean_email,
    email_sender,
    send_pending_confirmations,
    valid_email,
)
from intake.store import Store
from intake.web import CONFIRM_LIMIT

ADDRESS = "Shortlist, 123 College Ave, New Brunswick, NJ 08901"

# The footer, verbatim. Written out here rather than imported from the
# module under test: the copy is the compliance artifact, so a test that
# reads it from the same constant would agree with any edit at all.
REASON = ("This email was sent to you because you signed up for Software "
          "Engineering Internship Alerts matching your preferred companies.")
PREFERENCES = "Adjust the companies you follow or stop every alert at any time."


def cfg(**over) -> NotifySettings:
    base = {"postal_address": ADDRESS, "region": "us-east-2"}
    return NotifySettings(**{**base, **over})


class FakeSES:
    """Records send_email calls; raises what it is told to raise."""

    def __init__(self, error: Exception | None = None):
        self.calls: list[dict] = []
        self.error = error

    def send_email(self, **params):
        self.calls.append(params)
        if self.error:
            raise self.error
        return {"MessageId": "0100-fake"}


class AwsError(Exception):
    """Shaped like a botocore ClientError, which cannot be imported here."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.response = {"Error": {"Code": code, "Message": message}}


# Real tokens are uuid4().hex. The length matters to the assertions below:
# a short stand-in fits inside one 78-column header line and hides the
# folding behavior that a real one triggers.
TOKEN = "89dcf0f2d2864f77833a2f386acf530c"
VERIFY_TOKEN = "b74365ce590f41d28bea2b5a92a632b8"


def sub(sid=1, target="student@scarletmail.rutgers.edu"):
    return {"id": sid, "channel": "email", "target": target,
            "token": TOKEN, "verify_token": VERIFY_TOKEN,
            "verified": True, "filters": {}}


def posting(company="NVIDIA", title="Software Engineer Intern",
            canonical="https://nvidia.com/careers/1", locations=("Santa Clara, CA",)):
    p = Posting.from_detection(RawDetection(
        source=Source.WORKDAY, company=company, title=title,
        url="https://aggregator.example/x", locations=list(locations),
    ))
    p.canonical_url = canonical
    return p


def sent_message(client) -> email.message.Message:
    """The MIME message SES was handed, parsed back."""
    return email.message_from_bytes(client.calls[-1]["Content"]["Raw"]["Data"])


def parts(msg) -> dict[str, str]:
    return {
        p.get_content_type(): p.get_payload(decode=True).decode()
        for p in msg.walk() if not p.is_multipart()
    }


def footer(text: str) -> list[str]:
    """The last four non-empty lines of a text part: the footer, in order."""
    return [line for line in text.splitlines() if line.strip()][-4:]


def order_of(html: str, marks: tuple[str, ...]) -> list[int]:
    """Where each mark sits in the HTML part. Every mark must be present:
    index raises otherwise, which is the assertion."""
    return [html.index(m) for m in marks]


# -- address validation ---------------------------------------------------

@pytest.mark.parametrize("value,ok", [
    ("af1174@scarletmail.rutgers.edu", True),
    ("a.b+tag@sub.example.co.uk", True),
    ("", False),
    ("nobody", False),
    ("a@b", False),                       # no dotted domain
    ("a b@example.com", False),           # space
    ("a@example..com", False),
    ("a@-example.com", False),
    ("two@example.com, other@example.com", False),
    ("x" * 250 + "@example.com", False),  # over the 254-char path limit
])
def test_email_shape_is_validated(value, ok):
    assert valid_email(value) is ok


def test_control_characters_are_stripped_not_carried():
    # The header-injection vector: a newline in the address would let a
    # submitter write their own headers into the message.
    assert clean_email(" a@b.edu\r\nBcc: victim@x.com ") == "a@b.eduBcc: victim@x.com"
    assert valid_email("a@b.edu\r\nBcc: victim@x.com") is False


# -- request shape --------------------------------------------------------

def test_alert_request_carries_every_required_element():
    client = FakeSES()
    sender = EmailSender(cfg(configuration_set="shortlist-events"), client)
    sender(sub(), [posting(), posting("Stripe", "ML Intern",
                                      "https://stripe.com/jobs/2", ())])

    assert len(client.calls) == 1
    params = client.calls[0]
    assert params["FromEmailAddress"] == "Shortlist <alerts@notify.short-list.app>"
    assert params["Destination"] == {
        "ToAddresses": ["student@scarletmail.rutgers.edu"]}
    assert params["ConfigurationSetName"] == "shortlist-events"
    # Raw MIME, not Simple: custom headers are the whole reason.
    assert set(params["Content"]) == {"Raw"}

    msg = sent_message(client)
    assert msg["Subject"] == (
        "[Alert] 2 new SWE internship postings at NVIDIA and Stripe")
    assert msg["From"] == "Shortlist <alerts@notify.short-list.app>"
    assert msg["To"] == "student@scarletmail.rutgers.edu"
    assert msg["Date"]                      # RFC 5322 requires one
    # Gmail and Yahoo require both of these from bulk senders.
    unsub = f"https://short-list.app/api/unsubscribe?token={TOKEN}"
    assert msg["List-Unsubscribe"] == f"<{unsub}>"
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"

    body = parts(msg)
    assert set(body) == {"text/plain", "text/html"}
    for part in body.values():
        assert ADDRESS in part          # CAN-SPAM
        assert unsub in part
        assert "https://nvidia.com/careers/1" in part
        assert "aggregator.example" not in part   # canonical_url, never the
        assert "Santa Clara, CA" in part          # link a list detector found
    assert "Stripe" in body["text/plain"] and "ML Intern" in body["text/plain"]


def test_the_unsubscribe_header_reaches_the_wire_unencoded():
    """The stdlib's default answer to a header too long to fold is to RFC
    2047 encode the whole value. A 32-hex token pushes List-Unsubscribe past
    78 columns with no whitespace to break at, and an encoded-word value is
    a URL no one-click parser can read, so the raw bytes are checked here
    rather than the parsed header."""
    client = FakeSES()
    EmailSender(cfg(), client)(sub(), [posting()])
    raw = client.calls[0]["Content"]["Raw"]["Data"]

    assert f"<https://short-list.app/api/unsubscribe?token={TOKEN}>".encode() in raw
    assert b"List-Unsubscribe-Post: List-Unsubscribe=One-Click" in raw
    assert b"=?utf-8?q?" not in raw
    # Headers unfolded must still leave every line inside SMTP's limit.
    assert max(len(line) for line in raw.split(b"\r\n")) < 998


def test_a_long_posting_url_stays_inside_the_line_limit():
    client = FakeSES()
    long_url = ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternal"
                "CareerSite/job/US-CA-Santa-Clara/" + "Segment-" * 60)
    EmailSender(cfg(), client)(sub(), [posting(canonical=long_url)])
    raw = client.calls[0]["Content"]["Raw"]["Data"]

    assert max(len(line) for line in raw.split(b"\r\n")) < 998
    # Soft line breaks in transit, one whole URL again after decoding.
    assert long_url in parts(sent_message(client))["text/plain"]


def test_no_configuration_set_means_the_key_is_absent():
    client = FakeSES()
    EmailSender(cfg(), client)(sub(), [posting()])
    assert "ConfigurationSetName" not in client.calls[0]


def test_confirmation_is_one_message_holding_the_verify_link():
    client = FakeSES()
    sender = EmailSender(cfg(), client)
    assert sender.send_confirmation(sub()) is True

    assert len(client.calls) == 1
    msg = sent_message(client)
    assert msg["Subject"] == "Confirm your Shortlist alerts"
    link = f"https://short-list.app/api/verify?token={VERIFY_TOKEN}"
    for part in parts(msg).values():
        assert link in part
        assert ADDRESS in part
    # The confirmation is bulk mail too, so it carries the same headers.
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_base_url_override_moves_every_link():
    client = FakeSES()
    sender = EmailSender(cfg(base_url="https://staging.example"), client)
    sender.send_confirmation(sub())
    assert "https://staging.example/api/verify?token=" in parts(sent_message(client))["text/plain"]


# -- the footer -----------------------------------------------------------

UNSUB = f"https://short-list.app/api/unsubscribe?token={TOKEN}"


@pytest.mark.parametrize("kind", ["alert", "confirmation"])
def test_every_message_carries_the_same_four_footer_elements(kind):
    """Reason for receipt, postal address, what the reader can change, and
    the unsubscribe link. Same four in the same order in both MIME parts of
    both message types, because a reader on a text-only client is owed the
    same notice as one reading the HTML."""
    client = FakeSES()
    sender = EmailSender(cfg(), client)
    if kind == "alert":
        sender(sub(), [posting()])
    else:
        sender.send_confirmation(sub())
    body = parts(sent_message(client))

    assert footer(body["text/plain"]) == [
        REASON,
        f"Our Mailing Address: {ADDRESS}",
        PREFERENCES,
        f"Unsubscribe instantly: {UNSUB}",
    ]
    # The HTML part carries the same four, in the same order, with the link
    # labeled rather than pasted raw.
    marks = (REASON, f"Our Mailing Address: {ADDRESS}", PREFERENCES,
             f'<a href="{UNSUB}">Unsubscribe instantly</a>')
    positions = order_of(body["text/html"], marks)
    assert positions == sorted(positions)


def test_the_footer_carries_the_configured_address_not_a_placeholder():
    client = FakeSES()
    other = "Shortlist, 1 Elm St, Newark, NJ 07102"
    EmailSender(cfg(postal_address=other), client)(sub(), [posting()])
    body = parts(sent_message(client))
    assert footer(body["text/plain"])[1] == f"Our Mailing Address: {other}"
    assert other in body["text/html"] and ADDRESS not in body["text/html"]


# -- subjects -------------------------------------------------------------

# (postings as (company, title), the subject that is true of them)
SUBJECT_CASES = [
    ([("Apple", "SWE Intern")],
     "[Alert] New SWE internship posting at Apple"),
    ([("Apple", "SWE Intern"), ("Apple", "iOS Intern"), ("Apple", "ML Intern")],
     "[Alert] 3 new SWE internship postings at Apple"),
    ([("Apple", "SWE Intern"), ("Stripe", "ML Intern")],
     "[Alert] 2 new SWE internship postings at Apple and Stripe"),
    # Three companies: two named, the rest counted. Naming every company
    # would run a subject past what any client shows.
    ([("Apple", "SWE Intern"), ("Stripe", "ML Intern"), ("NVIDIA", "GPU Intern")],
     "[Alert] 3 new SWE internship postings at Apple, Stripe and 1 more company"),
    ([("Apple", "SWE Intern"), ("Apple", "iOS Intern"), ("Stripe", "ML Intern"),
      ("NVIDIA", "GPU Intern"), ("Datadog", "Backend Intern")],
     "[Alert] 5 new SWE internship postings at Apple, Stripe and 2 more companies"),
]


@pytest.mark.parametrize("rows,subject", SUBJECT_CASES)
def test_the_subject_states_exactly_what_the_message_holds(rows, subject):
    postings = [posting(company=c, title=t, canonical=f"https://x.co/job/{i}")
                for i, (c, t) in enumerate(rows)]
    client = FakeSES()
    EmailSender(cfg(), client)(sub(), postings)
    msg = sent_message(client)

    assert msg["Subject"] == subject
    assert alert_subject(postings) == subject
    # The claim in the subject is checkable against the payload: every
    # posting counted is a posting enclosed, and every company named sent one.
    text = parts(msg)["text/plain"]
    for p in postings:
        assert p.canonical_url in text and p.title in text
    for named in ("Apple", "Stripe"):
        assert (named in subject) is any(p.company == named for p in postings)


def test_the_count_in_the_subject_is_postings_not_companies():
    # Two companies, four postings: the number a reader sees is the number
    # of things to look at, never the number of employers.
    postings = [posting(company="Apple", title=f"Intern {i}",
                        canonical=f"https://x.co/a{i}") for i in range(3)]
    postings.append(posting(company="Stripe", title="ML Intern",
                            canonical="https://x.co/s"))
    assert alert_subject(postings) == (
        "[Alert] 4 new SWE internship postings at Apple and Stripe")


# -- refusals and failures ------------------------------------------------

def test_missing_postal_address_refuses_to_send(caplog):
    client = FakeSES()
    sender = EmailSender(cfg(postal_address=""), client)
    with caplog.at_level(logging.ERROR, logger="intake"):
        sender(sub(), [posting()])
        assert sender.send_confirmation(sub()) is False
    # Nothing left the process, and the reason is loud rather than silent.
    assert client.calls == []
    assert "NOTIFY_POSTAL_ADDRESS" in caplog.text
    assert "subscription 1" in caplog.text


def test_sandbox_rejection_is_named_and_not_retried(caplog, tmp_path):
    store = Store(tmp_path / "s.db")
    sid, token, _ = store.add_subscription("email", "a@b.edu", {})
    client = FakeSES(AwsError(
        "MessageRejected",
        "Email address is not verified. The following identities failed the "
        "check in region US-EAST-2: alerts@notify.short-list.app",
    ))
    sender = EmailSender(cfg(), client, store=store)
    with caplog.at_level(logging.ERROR, logger="intake"):
        sender({**sub(sid), "token": token}, [posting()])

    assert "NOTIFY-SANDBOX" in caplog.text
    assert "production access" in caplog.text
    # The account is at fault, not the subscriber: the subscription survives.
    assert [s["id"] for s in store.active_subscriptions()] == [sid]
    assert len(client.calls) == 1  # attempted once, never retried in-place


def test_permanent_rejection_deactivates_that_subscription(caplog, tmp_path):
    store = Store(tmp_path / "d.db")
    sid, token, _ = store.add_subscription("email", "a@b.edu", {})
    client = FakeSES(AwsError("MessageRejected", "Recipient address rejected"))
    sender = EmailSender(cfg(), client, store=store)
    with caplog.at_level(logging.ERROR, logger="intake"):
        sender({**sub(sid), "token": token}, [posting()])

    # Retrying a permanent rejection every cycle is how a bounce rate
    # crosses 5% and costs the whole account its sending.
    assert "NOTIFY-PERMANENT" in caplog.text
    assert store.active_subscriptions() == []


def test_transient_failure_is_contained_and_logs_no_address(caplog):
    client = FakeSES(AwsError("ThrottlingException", "Maximum sending rate exceeded"))
    with caplog.at_level(logging.WARNING, logger="intake"):
        EmailSender(cfg(), client)(sub(), [posting()])   # must not raise
    assert "NOTIFY-FAIL" in caplog.text and "ThrottlingException" in caplog.text
    assert "student@scarletmail.rutgers.edu" not in caplog.text


def test_account_level_pause_reads_as_sandbox_not_as_a_bad_recipient(caplog, tmp_path):
    store = Store(tmp_path / "a.db")
    sid, token, _ = store.add_subscription("email", "a@b.edu", {})
    client = FakeSES(AwsError("AccountSuspendedException", "sending is paused"))
    with caplog.at_level(logging.ERROR, logger="intake"):
        EmailSender(cfg(), client, store=store)({**sub(sid), "token": token},
                                                [posting()])
    assert "NOTIFY-SANDBOX" in caplog.text
    assert [s["id"] for s in store.active_subscriptions()] == [sid]


def test_a_push_subscription_is_not_mailed():
    client = FakeSES()
    EmailSender(cfg(), client)({**sub(), "channel": "push"}, [posting()])
    assert client.calls == []


# -- pending confirmations ------------------------------------------------

def waiting_rows(store, n: int, prefix: str) -> list[int]:
    """n email signups that were never mailed a confirmation, which is what
    every signup looks like while the email channel is dark."""
    return [store.add_subscription("email", f"{prefix}{i}@x.edu", {})[0]
            for i in range(n)]


def stamps(store) -> list[str | None]:
    return [r["confirmation_sent_at"] for r in store.conn.execute(
        "SELECT confirmation_sent_at FROM subscriptions ORDER BY id")]


def test_pending_confirmations_are_mailed_once_sending_works(tmp_path):
    store = Store(tmp_path / "pend.db")
    ids = waiting_rows(store, 2, "wait")
    client = FakeSES()
    sender = EmailSender(cfg(), client, store=store)
    CONFIRM_LIMIT.reset()

    assert [s["id"] for s in store.pending_confirmations()] == ids
    assert send_pending_confirmations(store, sender) == 2
    assert len(client.calls) == 2
    # Each message is that row's own confirmation, carrying its verify link.
    for call in client.calls:
        raw = call["Content"]["Raw"]["Data"].decode()
        assert "/api/verify?token=" in raw

    # Stamped on acceptance, so the row leaves the queue and the next cycle
    # mails nobody twice.
    assert None not in stamps(store)
    assert store.pending_confirmations() == []
    assert send_pending_confirmations(store, sender) == 0
    assert len(client.calls) == 2


def test_a_refused_confirmation_stays_pending_for_the_next_cycle(tmp_path):
    store = Store(tmp_path / "refuse.db")
    waiting_rows(store, 1, "again")
    CONFIRM_LIMIT.reset()
    failing = EmailSender(cfg(), FakeSES(AwsError("ThrottlingException", "slow")),
                          store=store)
    assert send_pending_confirmations(store, failing) == 0
    # No stamp means no lost signup: the row is still owed a confirmation.
    assert stamps(store) == [None]

    client = FakeSES()
    assert send_pending_confirmations(store, EmailSender(cfg(), client, store=store)) == 1
    assert len(client.calls) == 1 and stamps(store) != [None]


@pytest.mark.parametrize("sender_for", [
    lambda client, store: LogSender(),                       # no SES at all
    lambda client, store: EmailSender(cfg(postal_address=""), client, store=store),
])
def test_the_backfill_is_a_no_op_while_sending_is_unconfigured(sender_for, tmp_path):
    store = Store(tmp_path / "dark.db")
    waiting_rows(store, 2, "dark")
    client = FakeSES()
    CONFIRM_LIMIT.reset()

    # Nothing sent and nothing attempted: an unsendable message must not
    # turn into a refusal logged once per row per cycle either.
    assert send_pending_confirmations(store, sender_for(client, store)) == 0
    assert client.calls == []
    assert stamps(store) == [None, None]
    assert len(store.pending_confirmations()) == 2


def test_the_backfill_drains_a_backlog_in_bounded_batches(tmp_path):
    store = Store(tmp_path / "backlog.db")
    waiting_rows(store, 8, "many")
    client = FakeSES()
    sender = EmailSender(cfg(), client, store=store)
    CONFIRM_LIMIT.reset()

    # A launch-day backlog arrives as several small cycles, not one burst.
    assert send_pending_confirmations(store, sender) == 5
    assert len(store.pending_confirmations()) == 3
    assert send_pending_confirmations(store, sender) == 3
    assert store.pending_confirmations() == []
    assert len(client.calls) == 8
    assert None not in stamps(store)


def test_one_address_with_several_pending_rows_is_not_mailed_at_once(tmp_path):
    store = Store(tmp_path / "same.db")
    for _ in range(3):
        store.add_subscription("email", "eager@x.edu", {})
    client = FakeSES()
    CONFIRM_LIMIT.reset()

    # The subscribe endpoint's per-recipient window, reused: three rows for
    # one address is still one inbox, and two confirmations is already the
    # most anyone needs.
    assert send_pending_confirmations(store, EmailSender(cfg(), client, store=store)) == 2
    assert len(store.pending_confirmations()) == 1
    CONFIRM_LIMIT.reset()


def test_confirmed_and_push_rows_are_never_in_the_backfill(tmp_path):
    store = Store(tmp_path / "skip.db")
    _, _, verify = store.add_subscription("email", "ok@x.edu", {})
    store.verify_by_token(verify)
    store.add_subscription("push", '{"endpoint": "https://p.example/x"}', {})
    _, token, _ = store.add_subscription("email", "off@x.edu", {})
    store.deactivate_by_token(token)

    # A confirmed address has nothing to confirm, push has no confirmation
    # step, and an unsubscribed row is not owed mail.
    assert store.pending_confirmations() == []


# -- construction ---------------------------------------------------------

def test_missing_boto3_degrades_to_the_log_sender(monkeypatch, caplog):
    def no_boto3(region):
        raise ImportError("No module named 'boto3'")

    monkeypatch.setattr("intake.senders._sesv2_client", no_boto3)
    with caplog.at_level(logging.WARNING, logger="intake"):
        assert email_sender(cfg()) is None
        assert isinstance(build_sender(cfg()), LogSender)
    assert "email channel off" in caplog.text


def test_the_client_is_built_for_the_configured_region(monkeypatch):
    seen = {}

    def fake_client(region):
        seen["region"] = region
        return FakeSES()

    monkeypatch.setattr("intake.senders._sesv2_client", fake_client)
    sender = email_sender(cfg(region="eu-west-1"))
    assert isinstance(sender, EmailSender) and seen["region"] == "eu-west-1"


def test_an_injected_client_wins_and_carries_the_store(tmp_path):
    store = Store(tmp_path / "i.db")
    client = FakeSES()
    sender = build_sender(cfg(), client, store)
    assert isinstance(sender, EmailSender)
    assert sender.client is client and sender.store is store
