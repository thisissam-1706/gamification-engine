"""Retroactive abuse detection pass over stored facts; flag-only.

The pass is separate from ingestion (ADR 0003). It reads accepted completions
and awards from the event store through the application's notification log and
ingestion evidence from ``EvidenceStore.list()``, runs the pure detectors and
stores signals. It never writes to the event store or the evidence store and
never changes XP, streaks, leaderboards or any other learner state.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.abuse.detectors import CompletionFact, EvidenceFact, Signal, detect_all
from src.abuse.rules import DEFAULT_ABUSE_RULES_VERSION, AbuseRules, get_abuse_rules
from src.abuse.store import AbuseSignalStore
from src.ingestion.evidence import EvidenceStore
from src.state_machine.learner import Learner, LearnerApplication


@dataclass(frozen=True)
class RunSummary:
    signals: list[Signal]
    counts_by_type: dict[str, int]
    learners_scanned: int
    events_read: int
    new_signals: int
    existing_signals: int
    shadow: bool
    rules_version: str
    skipped_completions_without_time: int = 0
    detected_at: str | None = field(default=None)


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def compare_runs(a: RunSummary, b: RunSummary) -> dict[str, list[tuple[str, str]]]:
    """Compares two runs by (learner_id, rule_id); signal ids differ across rule versions."""
    keys_a = {(s.learner_id, s.rule_id) for s in a.signals}
    keys_b = {(s.learner_id, s.rule_id) for s in b.signals}
    return {
        "only_in_a": sorted(keys_a - keys_b),
        "only_in_b": sorted(keys_b - keys_a),
        "in_both": sorted(keys_a & keys_b),
    }


class AbuseDetectionService:
    def __init__(
        self,
        application: LearnerApplication,
        evidence_store: EvidenceStore,
        signal_store: AbuseSignalStore,
        rules_version: str | AbuseRules = DEFAULT_ABUSE_RULES_VERSION,
    ) -> None:
        self.application = application
        self.evidence_store = evidence_store
        self.signal_store = signal_store
        self.rules = get_abuse_rules(rules_version)

    def _domain_events(self) -> Iterable[Any]:
        """Every stored domain event, via the notification log (as the leaderboard consumes it)."""
        log = self.application.notification_log
        position = 1
        while True:
            batch = list(log.select(start=position, limit=log.section_size))
            if not batch:
                return
            for notification in batch:
                yield self.application.mapper.to_domain_event(notification)
            position = batch[-1].id + 1

    def completion_facts(self) -> tuple[list[CompletionFact], int, int]:
        """Accepted completions joined with their awards: (facts, events_read, skipped)."""
        completed: dict[tuple[str, str], Any] = {}
        awards: dict[tuple[str, str], Any] = {}
        events_read = 0
        for event in self._domain_events():
            events_read += 1
            if isinstance(event, Learner.LessonCompleted):
                completed[(str(event.originator_id), str(event.completion_id))] = event
            elif isinstance(event, Learner.XPAwarded):
                awards[(str(event.originator_id), str(event.completion_id))] = event
        facts: list[CompletionFact] = []
        skipped = 0
        for (learner_id, completion_id), event in sorted(completed.items()):
            occurred = _parse_time(getattr(event, "occurred_at", None))
            if occurred is None:
                skipped += 1
                continue
            award = awards.get((learner_id, completion_id))
            event_id = getattr(event, "source_event_id", None)
            binding = self.evidence_store.delivery_binding(event_id) if event_id else None
            facts.append(
                CompletionFact(
                    learner_id=learner_id,
                    completion_id=completion_id,
                    event_id=event_id,
                    lesson_id=str(event.lesson_id),
                    completion_kind=getattr(award, "completion_kind", None),
                    occurred_at=occurred,
                    repeat_index=getattr(award, "repeat_index", None),
                    xp_amount=award.xp_amount if award is not None else 0,
                    received_at=_parse_time(binding.received_at) if binding else None,
                )
            )
        return facts, events_read, skipped

    def evidence_facts(self) -> list[EvidenceFact]:
        facts = []
        for record in self.evidence_store.list():
            received = _parse_time(record.received_at)
            if received is None:
                continue
            facts.append(
                EvidenceFact(
                    learner_id=record.learner_id,
                    reason=record.reason,
                    action_id=record.action_id,
                    event_id=record.event_id,
                    original_ref=record.original_ref,
                    received_at=received,
                    evidence_id=record.id,
                )
            )
        return facts

    def run(
        self,
        learner_ids: Iterable[Any] | None = None,
        *,
        shadow: bool = False,
        rules_version: str | AbuseRules | None = None,
        detected_at: str | None = None,
    ) -> RunSummary:
        """Detects and stores signals; with ``shadow=True`` it returns them without writing.

        ``detected_at`` defaults to the latest fact time scanned (data, not a clock).
        """
        rules = self.rules if rules_version is None else get_abuse_rules(rules_version)
        wanted = None if learner_ids is None else {str(i) for i in learner_ids}
        completions, events_read, skipped = self.completion_facts()
        evidence = self.evidence_facts()
        if wanted is not None:
            completions = [c for c in completions if c.learner_id in wanted]
            evidence = [e for e in evidence if e.learner_id in wanted]
        scanned = (
            len(wanted)
            if wanted is not None
            else len({c.learner_id for c in completions} | {e.learner_id for e in evidence})
        )
        signals = detect_all(rules, completions, evidence)
        stamp = detected_at
        if stamp is None:
            times = [c.occurred_at for c in completions] + [e.received_at for e in evidence]
            stamp = max(times).isoformat() if times else None
        new = existing = 0
        if not shadow and signals:
            new, existing = self.signal_store.save_many(signals, stamp or "")
        return RunSummary(
            signals=signals,
            counts_by_type=dict(sorted(Counter(s.signal_type for s in signals).items())),
            learners_scanned=scanned,
            events_read=events_read,
            new_signals=new,
            existing_signals=existing,
            shadow=shadow,
            rules_version=rules.version,
            skipped_completions_without_time=skipped,
            detected_at=stamp,
        )
