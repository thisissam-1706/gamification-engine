"""Demo notification helpers: provider-outage flags, learner preferences, dispatcher factory.

Must not import webapp.demo_backend (it is imported by demo_backend).
"""

from pathlib import Path
import sqlite3

from src.notifications.channels.base import BaseChannelAdapter, TransientProviderError
from src.notifications.channels.email import SimulatedEmailAdapter
from src.notifications.channels.in_app import InAppChannelAdapter
from src.notifications.channels.push import SimulatedPushAdapter
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
    return LearnerPreferences(
        learner_id=str(learner_id), timezone=timezone, opted_out_channels=opted_out
    )


def build_dispatcher(live_dir: Path, notif_store, clock) -> NotificationDispatcher:
    adapters = {
        ChannelType.IN_APP: InAppChannelAdapter(),
        ChannelType.PUSH: OutageAwareAdapter(live_dir, "outage_push", SimulatedPushAdapter()),
        ChannelType.EMAIL: OutageAwareAdapter(live_dir, "outage_email", SimulatedEmailAdapter()),
    }
    return NotificationDispatcher(
        notif_store,
        adapters=adapters,
        retry_scheduler=RetryScheduler(max_retries=3),
        clock=clock,
    )
