"""Email delivery through Amazon SES. The live end of notify.py's Sender seam.

Transport choices, and why they are not free choices:

- **SESv2, raw MIME.** The Simple content shape has no dependable custom
  header support, and Gmail and Yahoo require List-Unsubscribe plus
  List-Unsubscribe-Post (RFC 8058) from bulk senders. The message is built
  with stdlib email.message.EmailMessage and handed over as raw bytes, so
  headers are ours and MIME costs no dependency.
- **No credentials in configuration.** The client is built with nothing but
  a region and lets boto3's default chain resolve: an IAM role on the
  instance (IMDSv2) in production, AWS_* variables on a laptop. The
  production role carries ses:SendEmail and nothing else, so there is no
  long-lived secret on the box to leak or rotate.
- **boto3 is an optional extra**, imported lazily the way store.py defers
  psycopg. A laptop or an image without it degrades to notify.LogSender
  rather than crashing the poller.
- **A physical postal address is mandatory** (CAN-SPAM). Unset means this
  sender refuses to send at all and says so loudly: a non-compliant message
  is worse than a missing one, and a quiet degradation would ship for weeks.

Failure handling is the other half of the job. A poll cycle must never die
because AWS returned an error, so every send is contained here and logged
against the subscription id, never the address. Three outcomes are told
apart because the operator's next action differs for each:

  sandbox   the account itself is not approved for general sending yet
            (MessageRejected naming an unverified identity, or an
            account-level pause). Nothing about the subscriber is wrong and
            nothing is retried; the fix is an AWS support request.
  permanent SES refuses this specific recipient. The subscription is
            deactivated instead of being retried every cycle, because a
            bounce rate over ~5% costs the whole account its sending.
  transient anything else. Logged, dropped, retried next cycle by nature.
"""

from __future__ import annotations

import logging
import re
from email import policy
from email.message import EmailMessage
from email.utils import formatdate
from html import escape
from urllib.parse import quote

from .config import NotifySettings, load_notify_settings
from .notify import LogSender, Sender
from .schema import Posting

log = logging.getLogger("intake")

# Two policies, on purpose. Building under an ordinary 78-column limit is
# what makes the content manager choose quoted-printable and keep body
# lines inside SMTP's 998-octet ceiling. Serializing with folding off is
# what keeps List-Unsubscribe intact: a 32-hex token makes that header
# longer than 78 columns with no whitespace to fold at, and the stdlib's
# answer to an unfoldable unstructured header is to RFC 2047 encode the
# whole value. Gmail's one-click parser would then see =?utf-8?q?...?=
# where a URL should be, which is silent non-compliance.
BUILD_POLICY = policy.SMTP
WIRE_POLICY = policy.SMTP.clone(max_line_length=0)

EMAIL_MAX = 254           # RFC 5321 path limit; anything longer is not an address
CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
# Deliberately narrower than RFC 5322: one @, a dotted domain of sane labels,
# no quoting, no comments, no display name. Every address a student can
# actually receive mail at passes, and the shapes that only exist to smuggle
# a header or a second recipient do not.
EMAIL_RE = re.compile(
    r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}"
    r"@[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)

# Substrings AWS puts in a MessageRejected whose cause is the account's own
# sandbox status rather than the recipient. Matched on the message because
# the error code is the same one a genuinely bad recipient produces.
SANDBOX_MARKERS = (
    "not verified", "identities failed the check", "sandbox",
    "account is not authorized", "not authorized to send",
)
# Error codes that are account-wide: sending is off, and no recipient is at
# fault. Same treatment as the sandbox, different sentence in the log.
ACCOUNT_ERROR_CODES = (
    "AccountSuspendedException", "SendingPausedException",
    "MailFromDomainNotVerifiedException",
)


def clean_email(value: str) -> str:
    """Untrusted text, flattened to one line and clamped. Control characters
    are the header-injection vector, so they are stripped before anything
    else looks at the value."""
    return CTRL_RE.sub("", value or "").strip()[:EMAIL_MAX]


def valid_email(value: str) -> bool:
    """Could this be delivered to? Shape only; the confirmation click is
    what proves the address exists and that its owner asked for mail."""
    v = clean_email(value)
    return bool(v) and len(v) <= EMAIL_MAX and bool(EMAIL_RE.match(v))


def _error_code(exc: Exception) -> str:
    """AWS error code without importing botocore. boto3 is an optional
    extra, so its exception classes cannot be caught by type here."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = (response.get("Error") or {}).get("Code")
        if code:
            return str(code)
    return type(exc).__name__


def _is_sandbox(code: str, message: str) -> bool:
    return code in ACCOUNT_ERROR_CODES or (
        code == "MessageRejected"
        and any(m in message.lower() for m in SANDBOX_MARKERS)
    )


def _sesv2_client(region: str):
    """The SESv2 client, built with no explicit credentials so boto3's
    default chain resolves the instance role. Import is deferred: boto3 is
    the `email` extra, absent from a lean install."""
    import boto3

    return boto3.client("sesv2", region_name=region)


class EmailSender:
    """notify.Sender for channel 'email', plus the double opt-in message.

    store is optional and touched for exactly one reason: retiring a
    subscription SES calls permanently undeliverable. The web server builds
    this without one, because the confirmation path has no subscription to
    retire yet and its SQLite connection belongs to a single request thread.
    """

    def __init__(self, cfg: NotifySettings, client, store=None):
        self.cfg = cfg
        self.client = client
        self.store = store

    # -- addresses --------------------------------------------------------

    def unsubscribe_url(self, token: str) -> str:
        return f"{self.cfg.base_url}/api/unsubscribe?token={quote(token)}"

    def verify_url(self, token: str) -> str:
        return f"{self.cfg.base_url}/api/verify?token={quote(token)}"

    # -- messages ---------------------------------------------------------

    def alert_message(self, sub: dict, postings: list[Posting]) -> EmailMessage:
        n = len(postings)
        subject = (
            f"{postings[0].company}: {postings[0].title}" if n == 1
            else f"{n} new internship postings"
        )
        unsub = self.unsubscribe_url(sub["token"])
        lead = "A new posting matched your alerts." if n == 1 else (
            f"{n} new postings matched your alerts."
        )
        text = [lead, ""]
        html = [f"<p>{escape(lead)}</p>"]
        for p in postings:
            # canonical_url is the employer's own page; url may still be the
            # aggregator link a list detector found it on.
            link = p.canonical_url or p.url
            where = ", ".join(p.locations[:3])
            text.append(f"{p.company}: {p.title}")
            if where:
                text.append(where)
            text.extend([link, ""])
            html.append(
                '<div style="margin:0 0 16px">'
                f'<div style="font-weight:600">{escape(p.company)}</div>'
                f'<div><a href="{escape(link, quote=True)}">{escape(p.title)}</a></div>'
                + (f'<div style="color:#6b7280">{escape(where)}</div>' if where else "")
                + "</div>"
            )
        return self._build(
            sub, subject, text, html,
            f"You subscribed to new-posting alerts at {self.cfg.base_url}.",
            unsub,
        )

    def confirmation_message(self, sub: dict) -> EmailMessage:
        link = self.verify_url(sub["verify_token"])
        return self._build(
            sub,
            "Confirm your Shortlist alerts",
            ["Confirm this address to start receiving new-posting alerts:", "",
             link, "",
             "If you did not ask for this, ignore this message. Nothing is "
             "sent to an address that never confirms, and the request is "
             "deleted after seven days.", ""],
            ['<p>Confirm this address to start receiving new-posting alerts:</p>',
             f'<p><a href="{escape(link, quote=True)}">Confirm my alerts</a></p>',
             "<p>If you did not ask for this, ignore this message. Nothing is "
             "sent to an address that never confirms, and the request is "
             "deleted after seven days.</p>"],
            "You received this because this address was entered at "
            f"{self.cfg.base_url}.",
            self.unsubscribe_url(sub["token"]),
        )

    def _build(
        self, sub: dict, subject: str, text: list[str], html: list[str],
        why: str, unsub: str,
    ) -> EmailMessage:
        """One message, both parts, every compliance element in both.

        The postal address and the unsubscribe link are appended here rather
        than by each caller, so no message shape can ever ship without them.
        """
        msg = EmailMessage(policy=BUILD_POLICY)
        msg["From"] = self.cfg.sender
        msg["To"] = sub["target"]
        msg["Subject"] = subject
        # RFC 5322 requires a Date. SES would add one, but a mandatory
        # header should not depend on what the transport patches up.
        msg["Date"] = formatdate(usegmt=True)
        # RFC 8058: the POST target is the same /api/unsubscribe the frontend
        # calls, which takes the token from the query string precisely so a
        # mail client's one-click POST works with a body we never read.
        msg["List-Unsubscribe"] = f"<{unsub}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        msg.set_content(
            "\n".join([*text, why, f"Unsubscribe: {unsub}", self.cfg.postal_address])
        )
        msg.add_alternative(
            '<div style="font:14px/1.6 -apple-system,Segoe UI,Helvetica,Arial,'
            'sans-serif;color:#161616;max-width:560px">'
            + "".join(html)
            + f'<p style="color:#6b7280;font-size:12px">{escape(why)}<br>'
            f'<a href="{escape(unsub, quote=True)}">Unsubscribe</a><br>'
            f"{escape(self.cfg.postal_address)}</p></div>",
            subtype="html",
        )
        return msg

    # -- delivery ---------------------------------------------------------

    def __call__(self, sub: dict, postings: list[Posting]) -> None:
        """Sender seam. Never raises: a transport fault must not end a cycle."""
        if sub.get("channel") != "email" or not postings:
            return
        self._send(sub, self.alert_message(sub, postings))

    def send_confirmation(self, sub: dict) -> bool:
        """The one double opt-in message. True when SES accepted it, which
        is what the subscribe endpoint reports so the UI can say whether to
        go look in an inbox."""
        return self._send(sub, self.confirmation_message(sub))

    def _send(self, sub: dict, msg: EmailMessage) -> bool:
        sid = sub.get("id")
        if not self.cfg.postal_address:
            log.error(
                "NOTIFY-BLOCKED subscription %s: NOTIFY_POSTAL_ADDRESS is unset, "
                "so no mail may be sent (CAN-SPAM requires a physical address "
                "in every message)", sid,
            )
            return False
        params = {
            "FromEmailAddress": self.cfg.sender,
            "Destination": {"ToAddresses": [sub["target"]]},
            "Content": {"Raw": {"Data": msg.as_bytes(policy=WIRE_POLICY)}},
        }
        if self.cfg.configuration_set:
            params["ConfigurationSetName"] = self.cfg.configuration_set
        try:
            self.client.send_email(**params)
            return True
        except Exception as e:
            self._handle_failure(sub, e)
            return False

    def _handle_failure(self, sub: dict, exc: Exception) -> None:
        sid = sub.get("id")
        code, message = _error_code(exc), str(exc)
        if _is_sandbox(code, message):
            # The account, not the subscriber. Retrying cannot help and the
            # fix is a support request, so this line has to be unmistakable.
            log.error(
                "NOTIFY-SANDBOX subscription %s: SES refused the send because "
                "this account is not approved for general sending (%s). "
                "Request production access; not retried.", sid, code,
            )
            return
        if code == "MessageRejected":
            # Permanent for this recipient. Retrying it every cycle is how a
            # bounce rate crosses 5% and costs the account its sending.
            log.error(
                "NOTIFY-PERMANENT subscription %s: SES rejected the recipient "
                "(%s); deactivating the subscription", sid, code,
            )
            if self.store is not None and sub.get("token"):
                self.store.deactivate_by_token(sub["token"])
            return
        log.warning("NOTIFY-FAIL subscription %s: %s", sid, code)


def email_sender(
    cfg: NotifySettings | None = None, client=None, store=None,
) -> EmailSender | None:
    """The live email sender, or None when SES cannot be reached at all.

    None is the ordinary state of a laptop and of the test suite: no boto3,
    no region, no role. Callers fall back rather than fail.
    """
    cfg = cfg or load_notify_settings()
    if client is None:
        try:
            client = _sesv2_client(cfg.region)
        except Exception as e:  # ImportError, or botocore refusing the region
            log.warning("email channel off: no SES client (%s: %s)",
                        type(e).__name__, e)
            return None
    return EmailSender(cfg, client, store=store)


def build_sender(
    cfg: NotifySettings | None = None, client=None, store=None,
) -> Sender:
    """The pipeline's sender: SES when it can be built, else the log
    placeholder that has always held this seam."""
    return email_sender(cfg, client, store) or LogSender()
