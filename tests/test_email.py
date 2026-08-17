"""Email delivery over SES: the exact request shape, the compliance
elements every message must carry, and the failure paths.

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
    build_sender,
    clean_email,
    email_sender,
    valid_email,
)
from intake.store import Store

ADDRESS = "Shortlist, 123 College Ave, New Brunswick, NJ 08901"


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
    assert params["FromEmailAddress"] == "Shortlist <alerts@short-list.app>"
    assert params["Destination"] == {
        "ToAddresses": ["student@scarletmail.rutgers.edu"]}
    assert params["ConfigurationSetName"] == "shortlist-events"
    # Raw MIME, not Simple: custom headers are the whole reason.
    assert set(params["Content"]) == {"Raw"}

    msg = sent_message(client)
    assert msg["Subject"] == "2 new internship postings"
    assert msg["From"] == "Shortlist <alerts@short-list.app>"
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


def test_a_single_posting_gets_a_specific_subject():
    client = FakeSES()
    EmailSender(cfg(), client)(sub(), [posting()])
    assert sent_message(client)["Subject"] == "NVIDIA: Software Engineer Intern"


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
        "check in region US-EAST-2: alerts@short-list.app",
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
