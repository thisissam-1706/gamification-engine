"""SmtpEmailAdapter against a fake SMTP server object (no network)."""

import smtplib

import pytest

from src.notifications.channels.base import PermanentProviderError, TransientProviderError
from src.notifications.channels.smtp import SmtpAccount, SmtpEmailAdapter
from src.notifications.models import ChannelType, NotificationCategory, NotificationProposal

ACCOUNT = SmtpAccount(
    host="smtp.ethereal.email", port=587, user="u@ethereal.email", password="pw",
    sender="Streakwise <u@ethereal.email>", preview_base="https://ethereal.email/message/",
)


def _proposal(**payload):
    return NotificationProposal(
        proposal_id="p1", learner_id="L", channel=ChannelType.EMAIL,
        category=NotificationCategory.STREAK_RISK,
        payload={"title": "Keep your streak", "body": "One lesson today.", "to": "priya@learner.example", **payload},
    )


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout=None, *, fail=None):
        self.host, self.port, self.calls, self.data_bytes = host, port, [], None
        FakeSMTP.instances.append(self)

    def ehlo(self): self.calls.append("ehlo")
    def starttls(self): self.calls.append("starttls")
    def login(self, user, password): self.calls.append(("login", user))
    def mail(self, sender): self.calls.append(("mail", sender)); return 250, b"ok"
    def rcpt(self, to): self.calls.append(("rcpt", to)); return 250, b"ok"

    def data(self, raw):
        self.data_bytes = raw
        return 250, b"Accepted [STATUS=new MSGID=YxZ-123.abc]"

    def quit(self): self.calls.append("quit")


def test_sends_over_starttls_and_exposes_the_preview_url():
    FakeSMTP.instances.clear()
    adapter = SmtpEmailAdapter(ACCOUNT, smtp_factory=FakeSMTP)
    assert adapter.send(_proposal()) is True
    server = FakeSMTP.instances[-1]
    assert (server.host, server.port) == ("smtp.ethereal.email", 587)
    assert "starttls" in server.calls and ("rcpt", "priya@learner.example") in server.calls
    assert b"Subject: Keep your streak" in server.data_bytes and b"X-Proposal-Id: p1" in server.data_bytes
    assert adapter.last_message_id == "YxZ-123.abc"
    assert adapter.last_preview_url == "https://ethereal.email/message/YxZ-123.abc"


def test_connect_failure_is_transient_so_the_dispatcher_retries_then_dead_letters():
    def refuse(*a, **k):
        raise ConnectionRefusedError("no route")
    with pytest.raises(TransientProviderError):
        SmtpEmailAdapter(ACCOUNT, smtp_factory=refuse).send(_proposal())


def test_bad_login_is_permanent():
    class BadLogin(FakeSMTP):
        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
    with pytest.raises(PermanentProviderError):
        SmtpEmailAdapter(ACCOUNT, smtp_factory=BadLogin).send(_proposal())


def test_temporary_4xx_is_transient_and_missing_recipient_is_permanent():
    class Busy(FakeSMTP):
        def data(self, raw):
            raise smtplib.SMTPDataError(451, b"try later")
    with pytest.raises(TransientProviderError):
        SmtpEmailAdapter(ACCOUNT, smtp_factory=Busy).send(_proposal())
    with pytest.raises(PermanentProviderError):
        SmtpEmailAdapter(ACCOUNT, smtp_factory=FakeSMTP).send(_proposal(to=""))


def test_local_capture_server_needs_no_starttls_or_login():
    """Mailpit-style server: plain SMTP on 1025, no STARTTLS, no login."""
    FakeSMTP.instances.clear()
    local = SmtpAccount(host="localhost", port=1025, user="", password="", sender="s@demo.local", starttls=False)
    assert SmtpEmailAdapter(local, smtp_factory=FakeSMTP).send(_proposal()) is True
    calls = FakeSMTP.instances[-1].calls
    assert "starttls" not in calls and not any(isinstance(c, tuple) and c[0] == "login" for c in calls)
