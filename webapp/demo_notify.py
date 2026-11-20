"""Demo notification helpers: provider-outage flags, learner preferences, dispatcher factory,
the delivered-messages outbox and the fake-inbox email account.

Where messages go in the demo:
  in_app -> the learner's inbox on the Learner home page (notification_dispatches rows).
  push   -> a simulated phone: there is no real push provider (that needs Firebase/APNs and a
            device token), so each delivered push is written to the outbox and shown as a phone
            notification in the admin Notifications tab.
  email  -> a real SMTP server when an email account is configured (Ethereal: a free fake
            inbox that accepts mail and never delivers it), otherwise the local outbox only.

Must not import webapp.demo_backend (it is imported by demo_backend).
"""

import json
import os
from pathlib import Path
import sqlite3
import urllib.request

from src.notifications.channels.base import BaseChannelAdapter, TransientProviderError
from src.notifications.channels.email import SimulatedEmailAdapter
from src.notifications.channels.in_app import InAppChannelAdapter
from src.notifications.channels.push import SimulatedPushAdapter
from src.notifications.channels.smtp import SmtpAccount, SmtpEmailAdapter
from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.models import ChannelType, LearnerPreferences
from src.notifications.queue import RetryScheduler
from webapp import views


def _flags_db(live_dir: Path) -> Path:
    return Path(live_dir) / "demo_state.db"


def _ensure_flags(con: sqlite3.Connection) -> None:
    con.execute("CREATE TABLE IF NOT EXISTS demo_flags(key TEXT PRIMARY KEY, value TEXT)")


def get_flag(live_dir: Path, key: str) -> str | None:
    con = sqlite3.connect(str(_flags_db(live_dir)))
    try:
        _ensure_flags(con)
        row = con.execute("SELECT value FROM demo_flags WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
    finally:
        con.close()


def set_flag(live_dir: Path, key: str, value: str | None) -> None:
    con = sqlite3.connect(str(_flags_db(live_dir)))
    try:
        _ensure_flags(con)
        if value is None:
            con.execute("DELETE FROM demo_flags WHERE key = ?", (key,))
        else:
            con.execute(
                "INSERT INTO demo_flags (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------- outbox

_OUTBOX_DDL = """
CREATE TABLE IF NOT EXISTS demo_outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at TEXT,
    learner_id TEXT,
    channel TEXT,
    proposal_id TEXT,
    category TEXT,
    recipient TEXT,
    title TEXT,
    body TEXT,
    provider TEXT,
    provider_message_id TEXT,
    preview_url TEXT
)
"""


def _iso(value) -> str | None:
    if value is None:
        return None
    text = value.isoformat() if hasattr(value, "isoformat") else str(value)
    return text.replace("+00:00", "Z")


def record_outbox(live_dir: Path, row: dict) -> None:
    con = sqlite3.connect(str(_flags_db(live_dir)), timeout=10)
    try:
        con.execute(_OUTBOX_DDL)
        con.execute(
            "INSERT INTO demo_outbox (sent_at, learner_id, channel, proposal_id, category, recipient,"
            " title, body, provider, provider_message_id, preview_url) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (row.get("sent_at"), row.get("learner_id"), row.get("channel"), row.get("proposal_id"),
             row.get("category"), row.get("recipient"), row.get("title"), row.get("body"),
             row.get("provider"), row.get("provider_message_id"), row.get("preview_url")),
        )
        con.commit()
    finally:
        con.close()


def list_outbox(live_dir: Path, learner_id: str | None = None, limit: int = 50) -> list[dict]:
    con = sqlite3.connect(str(_flags_db(live_dir)), timeout=10)
    try:
        con.execute(_OUTBOX_DDL)
        sql = "SELECT * FROM demo_outbox"
        params: tuple = ()
        if learner_id is not None:
            sql += " WHERE learner_id = ?"
            params = (str(learner_id),)
        cur = con.execute(sql + " ORDER BY seq DESC LIMIT ?", (*params, int(limit)))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()


class OutboxRecordingAdapter(BaseChannelAdapter):
    """Wraps a provider adapter; after a successful send, writes the message to the outbox."""

    def __init__(self, live_dir: Path, inner: BaseChannelAdapter, provider: str, clock=None) -> None:
        self.live_dir = Path(live_dir)
        self.inner = inner
        self.provider = provider
        self.clock = clock

    def send(self, proposal) -> bool:
        ok = self.inner.send(proposal)
        if ok:
            payload = dict(proposal.payload)
            record_outbox(self.live_dir, {
                "sent_at": _iso(self.clock()) if self.clock else None,
                "learner_id": proposal.learner_id,
                "channel": proposal.channel.value,
                "proposal_id": proposal.proposal_id,
                "category": proposal.category.value,
                "recipient": (payload.get("to") if proposal.channel == ChannelType.EMAIL
                              else "learner's phone (simulated)"),
                "title": payload.get("title", ""),
                "body": payload.get("body", ""),
                "provider": self.provider,
                "provider_message_id": getattr(self.inner, "last_message_id", None),
                "preview_url": getattr(self.inner, "last_preview_url", None),
            })
        return ok


# ---------------------------------------------------------------- fake-inbox email account

ETHEREAL_API = "https://api.nodemailer.com/user"


def _account_file(live_dir: Path) -> Path:
    # Next to golden/ and live/, so "Reset demo" (which wipes live/) keeps the account.
    return Path(live_dir).parent / "email_account.json"


def load_email_account(live_dir: Path) -> dict | None:
    """The configured SMTP account: DEMO_SMTP_* environment variables first, then the saved file."""
    host = os.environ.get("DEMO_SMTP_HOST")
    if host:
        user = os.environ.get("DEMO_SMTP_USER", "")
        return {
            "provider": os.environ.get("DEMO_SMTP_PROVIDER", "smtp"),
            "host": host, "port": int(os.environ.get("DEMO_SMTP_PORT", "587")),
            "user": user, "pass": os.environ.get("DEMO_SMTP_PASS", ""),
            "from": os.environ.get("DEMO_SMTP_FROM", f"Streakwise <{user}>"),
            "secure": os.environ.get("DEMO_SMTP_SECURE") == "1",
            "web": os.environ.get("DEMO_SMTP_WEB"), "source": "environment",
        }
    path = _account_file(live_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    data["source"] = "file"
    return data


def save_email_account(live_dir: Path, account: dict | None) -> None:
    path = _account_file(live_dir)
    if account is None:
        if path.exists():
            path.unlink()
        return
    path.write_text(json.dumps(account, indent=2, sort_keys=True), encoding="utf-8")


def create_ethereal_account(live_dir: Path, timeout: float = 15.0) -> dict:
    """Creates a free Ethereal test inbox (what nodemailer.createTestAccount does) and saves it."""
    headers = {"Content-Type": "application/json"}
    if os.environ.get("ETHEREAL_API_KEY"):  # optional; only needed for Ethereal's inbound feature
        headers["Authorization"] = "Bearer " + os.environ["ETHEREAL_API_KEY"]
    request = urllib.request.Request(
        ETHEREAL_API,
        data=json.dumps({"requestor": "streakwise-demo", "version": "1.0.0"}).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if data.get("status") != "success":
        raise RuntimeError(f"Ethereal did not create an account: {data}")
    smtp = data.get("smtp") or {}
    account = {
        "provider": "ethereal",
        "host": smtp.get("host", "smtp.ethereal.email"),
        "port": int(smtp.get("port", 587)),
        "secure": bool(smtp.get("secure", False)),
        "user": data["user"], "pass": data["pass"],
        "from": f"Streakwise Demo <{data['user']}>",
        "web": data.get("web") or "https://ethereal.email",
    }
    save_email_account(live_dir, account)
    return account


def mailpit_account(host: str = "localhost", port: int = 1025, web: str = "http://localhost:8025") -> dict:
    """A local Mailpit capture server: every email lands in its web inbox, nothing leaves the machine."""
    return {"provider": "mailpit", "host": host, "port": int(port), "user": "", "pass": "",
            "from": "Streakwise Demo <streakwise@demo.local>", "secure": False, "starttls": False,
            "web": web}


def public_account(account: dict | None) -> dict | None:
    """The account for the admin page. Only a throwaway Ethereal inbox shows its password (it is
    needed to log in to the web inbox); any other SMTP account keeps it hidden."""
    if account is None:
        return None
    out = {k: v for k, v in account.items() if k != "pass"}
    out["has_password"] = bool(account.get("pass"))
    if account.get("provider") == "ethereal":
        out["login_password"] = account.get("pass")
    return out


def _email_adapter(live_dir: Path):
    account = load_email_account(live_dir)
    if not account:
        return SimulatedEmailAdapter(), "local outbox (no email account configured)"
    web = account.get("web") or None
    preview = (web.rstrip("/") + "/message/") if account.get("provider") == "ethereal" and web else None
    if account.get("provider") == "mailpit" and web:
        preview = None  # Mailpit has no per-message link from SMTP; the inbox link is enough
    smtp = SmtpAccount(
        host=account["host"], port=int(account["port"]), user=account.get("user", ""),
        password=account.get("pass", ""),
        sender=account.get("from") or account.get("user") or "streakwise@demo.local",
        secure=bool(account.get("secure")), starttls=account.get("starttls", True),
        web_url=web, preview_base=preview,
    )
    return SmtpEmailAdapter(smtp, timeout=8.0), f"{account.get('provider', 'smtp')} ({account['host']})"


class OutageAwareAdapter(BaseChannelAdapter):
    """Wraps an adapter; raises a transient 503 while the outage flag is "1"."""

    def __init__(self, live_dir: Path, flag_key: str, inner: BaseChannelAdapter) -> None:
        self.live_dir = Path(live_dir)
        self.flag_key = flag_key
        self.inner = inner

    def send(self, proposal) -> bool:
        if get_flag(self.live_dir, self.flag_key) == "1":
            raise TransientProviderError(
                f"Simulated {proposal.channel.value} provider outage (503 Service Unavailable)"
            )
        return self.inner.send(proposal)


def prefs_for(live_dir: Path, learner_id: str, timezone: str = "UTC") -> LearnerPreferences:
    profile = views.load_profile(Path(live_dir))
    info = profile["learners"].get(str(learner_id), {})
    opted_out = set() if info.get("push_enabled", True) else {ChannelType.PUSH}
    # The learner's fake address (the .example domain never delivers), so reminders also go by email.
    name = info.get("display_name")
    email = name.lower().replace(" ", ".") + "@learner.example" if name else None
    return LearnerPreferences(
        learner_id=str(learner_id), timezone=timezone, opted_out_channels=opted_out, email=email
    )


def build_dispatcher(live_dir: Path, notif_store, clock) -> NotificationDispatcher:
    email_inner, email_provider = _email_adapter(live_dir)
    adapters = {
        ChannelType.IN_APP: InAppChannelAdapter(),
        ChannelType.PUSH: OutageAwareAdapter(
            live_dir, "outage_push",
            OutboxRecordingAdapter(live_dir, SimulatedPushAdapter(), "simulated phone", clock),
        ),
        ChannelType.EMAIL: OutageAwareAdapter(
            live_dir, "outage_email",
            OutboxRecordingAdapter(live_dir, email_inner, email_provider, clock),
        ),
    }
    return NotificationDispatcher(
        notif_store,
        adapters=adapters,
        retry_scheduler=RetryScheduler(max_retries=3),
        clock=clock,
    )
