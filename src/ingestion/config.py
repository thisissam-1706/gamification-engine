"""Trusted ingress settings; never read from the source envelope."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.ingestion.validation import DEFAULT_INGEST_RULE_VERSION
from src.state_machine.rules import RULESETS

DEFAULT_LATE_WINDOW = timedelta(hours=72)


def utc_now() -> datetime:
    """The system clock, in UTC; the trusted receive time for ingestion."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class IngestConfig:
    rule_version: str
    # Deliveries received more than this long after occurred_at are rejected.
    # None disables the check (unbounded).
    late_window: timedelta | None = DEFAULT_LATE_WINDOW
    clock: Callable[[], datetime] = utc_now
    # Optional reward hold: (learner_id, occurred_at) -> reason or None. When it returns a
    # reason, a lesson completion is accepted but its XP is decided as 0 with that reason
    # stored (src/abuse/xp_hold.py). None: no hold (the default).
    xp_hold: Callable[[str, str], str | None] | None = None

    def __post_init__(self) -> None:
        if self.rule_version not in RULESETS:
            raise ValueError(f"Unknown rule version: {self.rule_version}")
        if self.late_window is not None and (
            not isinstance(self.late_window, timedelta)
            or self.late_window <= timedelta(0)
        ):
            raise ValueError(
                f"late_window must be a positive timedelta or None, got {self.late_window!r}"
            )
        if not callable(self.clock):
            raise ValueError(f"clock must be callable, got {self.clock!r}")
        if self.xp_hold is not None and not callable(self.xp_hold):
            raise ValueError(f"xp_hold must be callable or None, got {self.xp_hold!r}")


DEFAULT_CONFIG = IngestConfig(DEFAULT_INGEST_RULE_VERSION)
