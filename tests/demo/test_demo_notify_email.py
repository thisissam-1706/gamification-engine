"""Demo email wiring: Ethereal account creation (HTTP mocked), adapter choice, outbox recording."""

import io
import json
from datetime import datetime, timezone

from src.notifications.channels.email import SimulatedEmailAdapter
from src.notifications.channels.push import SimulatedPushAdapter
from src.notifications.channels.smtp import SmtpEmailAdapter
from src.notifications.models import ChannelType, NotificationCategory, NotificationProposal
from webapp import demo_notify

NOW = datetime(2026, 10, 16, 18, 0, tzinfo=timezone.utc)


def _live(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    return live


def test_create_ethereal_account_saves_it_outside_live_and_selects_smtp(tmp_path, monkeypatch):
    live = _live(tmp_path)
    sent = {}

    def fake_urlopen(request, timeout=None):
        sent["url"], sent["body"] = request.full_url, json.loads(request.data)
        return io.BytesIO(json.dumps({
            "status": "success", "user": "abc@ethereal.email", "pass": "secret",
            "smtp": {"host": "smtp.ethereal.email", "port": 587, "secure": False},
            "web": "https://ethereal.email",
        }).encode())

    monkeypatch.setattr(demo_notify.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.delenv("DEMO_SMTP_HOST", raising=False)
    account = demo_notify.create_ethereal_account(live)
    assert sent["url"] == demo_notify.ETHEREAL_API and sent["body"]["requestor"] == "streakwise-demo"
    assert (tmp_path / "email_account.json").exists()  # survives "Reset demo", which wipes live/
    assert account["host"] == "smtp.ethereal.email" and account["user"] == "abc@ethereal.email"
    public = demo_notify.public_account(demo_notify.load_email_account(live))
    assert "pass" not in public and public["login_password"] == "secret"
    adapter, provider = demo_notify._email_adapter(live)
    assert isinstance(adapter, SmtpEmailAdapter) and provider.startswith("ethereal")
    assert adapter.account.preview_base == "https://ethereal.email/message/"


def test_without_an_account_email_goes_to_the_local_outbox(tmp_path, monkeypatch):
    monkeypatch.delenv("DEMO_SMTP_HOST", raising=False)
    adapter, provider = demo_notify._email_adapter(_live(tmp_path))
    assert isinstance(adapter, SimulatedEmailAdapter) and "local outbox" in provider


def test_non_ethereal_password_is_never_shown(tmp_path, monkeypatch):
    monkeypatch.delenv("DEMO_SMTP_HOST", raising=False)
    live = _live(tmp_path)
    demo_notify.save_email_account(live, {"provider": "smtp", "host": "h", "port": 25, "user": "u", "pass": "p"})
    assert "login_password" not in demo_notify.public_account(demo_notify.load_email_account(live))


def test_delivered_push_is_recorded_in_the_outbox_at_the_demo_clock(tmp_path):
    live = _live(tmp_path)
    adapter = demo_notify.OutboxRecordingAdapter(live, SimulatedPushAdapter(), "simulated phone", lambda: NOW)
    adapter.send(NotificationProposal(
        proposal_id="p1", learner_id="learner-1", channel=ChannelType.PUSH,
        category=NotificationCategory.STREAK_RISK, payload={"title": "T", "body": "B", "to": "x@learner.example"},
    ))
    [row] = demo_notify.list_outbox(live, "learner-1")
    assert row["sent_at"] == "2026-10-16T18:00:00Z" and row["provider"] == "simulated phone"
    assert row["recipient"] == "learner's phone (simulated)" and row["title"] == "T"


def test_mailpit_preset_uses_plain_local_smtp(tmp_path, monkeypatch):
    monkeypatch.delenv("DEMO_SMTP_HOST", raising=False)
    live = _live(tmp_path)
    demo_notify.save_email_account(live, demo_notify.mailpit_account())
    adapter, provider = demo_notify._email_adapter(live)
    assert isinstance(adapter, SmtpEmailAdapter) and provider.startswith("mailpit")
    assert (adapter.account.host, adapter.account.port, adapter.account.starttls) == ("localhost", 1025, False)
