"""Pure rule-based abuse detectors over plain facts.

No database and no clock: every input is a frozen fact and every threshold
comes from ``AbuseRules``. Detection is flag-only; a signal is always
``suspected`` and never changes learner state. Results are sorted, so the same
facts in any order give the same signals.
"""

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import statistics
from typing import Any, TypeVar

from src.abuse.rules import AbuseRules

SIGNAL_TYPES = ("replay", "impossible_timing", "scripted_farming", "duplicate_exploit")
# Kinds that count as ordinary lessons; reviews and remediation are exempt from
# repeat and cadence rules because spaced repetition repeats lessons by design.
LESSON_KINDS = frozenset({"lesson", None})

T = TypeVar("T")


@dataclass(frozen=True)
class CompletionFact:
    learner_id: str
    completion_id: str
    event_id: str | None
    lesson_id: str
    completion_kind: str | None
    occurred_at: datetime
    repeat_index: int | None
    xp_amount: int
    # Trusted server receive time (IngestConfig.clock); None when unknown.
    received_at: datetime | None = None


@dataclass(frozen=True)
class EvidenceFact:
    learner_id: str
    reason: str
    action_id: str | None
    event_id: str | None
    original_ref: str | None
    received_at: datetime
    evidence_id: int


@dataclass(frozen=True)
class Signal:
    signal_id: str
    learner_id: str
    signal_type: str
    status: str
    rule_id: str
    rule_version: str
    rule_digest: str
    evidence_refs: tuple[str, ...]
    detected_window: tuple[str, str]

    def to_payload(self) -> dict[str, Any]:
        """Exactly the abuse_signal_raised.schema.json payload."""
        return {
            "signal_id": self.signal_id,
            "signal_type": self.signal_type,
            "status": self.status,
            "evidence_refs": list(self.evidence_refs),
        }


def _utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _group(items: Iterable[T], key: Callable[[T], str]) -> dict[str, list[T]]:
    groups: dict[str, list[T]] = defaultdict(list)
    for item in items:
        groups[key(item)].append(item)
    return groups


def _make_signal(
    rules: AbuseRules,
    learner_id: str,
    signal_type: str,
    rule_id: str,
    refs: Iterable[str],
    start: datetime,
    end: datetime,
) -> Signal:
    evidence_refs = tuple(sorted(set(refs)))
    if not evidence_refs:
        raise ValueError("a signal needs at least one evidence reference")
    digest = rules.digest
    refs_hash = hashlib.sha256(
        json.dumps(evidence_refs, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return Signal(
        signal_id=f"abuse:{rule_id}:{digest[:12]}:{learner_id}:{refs_hash}",
        learner_id=learner_id,
        signal_type=signal_type,
        status="suspected",
        rule_id=rule_id,
        rule_version=rules.version,
        rule_digest=digest,
        evidence_refs=evidence_refs,
        detected_window=(_utc(start).isoformat(), _utc(end).isoformat()),
    )


def _sorted_signals(signals: Iterable[Signal]) -> list[Signal]:
    return sorted(
        signals, key=lambda s: (s.learner_id, s.signal_type, s.rule_id, s.signal_id)
    )


def _completion_refs(completions: Iterable[CompletionFact]) -> list[str]:
    refs: list[str] = []
    for completion in completions:
        refs.append(f"completion:{completion.completion_id}")
        if completion.event_id:
            refs.append(f"event:{completion.event_id}")
    return refs


def _by_time(completions: Iterable[CompletionFact]) -> list[CompletionFact]:
    return sorted(completions, key=lambda c: (_utc(c.occurred_at), c.completion_id))


def _first_evidence_per_event(rows: Iterable[EvidenceFact]) -> list[EvidenceFact]:
    """One row per distinct event_id (the earliest), in time order."""
    ordered = sorted(rows, key=lambda r: (_utc(r.received_at), r.evidence_id))
    seen: set[str] = set()
    distinct: list[EvidenceFact] = []
    for row in ordered:
        if row.event_id is None or row.event_id in seen:
            continue
        seen.add(row.event_id)
        distinct.append(row)
    return distinct


def _earliest_window(
    rows: Sequence[EvidenceFact], count: int, window: timedelta
) -> Sequence[EvidenceFact] | None:
    """First ``count`` rows that fit in ``window`` (inclusive), earliest start first."""
    for start in range(len(rows) - count + 1):
        group = rows[start : start + count]
        if _utc(group[-1].received_at) - _utc(group[0].received_at) <= window:
            return group
    return None


def _evidence_signals(
    rules: AbuseRules,
    evidence: Iterable[EvidenceFact],
    signal_type: str,
    reason: str,
    rule_id: str,
    window_minutes: int,
    minimum: int,
    any_reason: str,
    any_rule_id: str,
    any_enabled: bool,
) -> list[Signal]:
    signals: list[Signal] = []
    relevant = [row for row in evidence if row.reason in (reason, any_reason)]
    for learner_id, rows in _group(relevant, lambda r: r.learner_id).items():
        group = _earliest_window(
            _first_evidence_per_event([r for r in rows if r.reason == reason]),
            minimum,
            timedelta(minutes=window_minutes),
        )
        if group is not None:
            signals.append(
                _make_signal(
                    rules,
                    learner_id,
                    signal_type,
                    rule_id,
                    (f"evidence:{row.evidence_id}" for row in group),
                    group[0].received_at,
                    group[-1].received_at,
                )
            )
        anomalies = sorted(
            (r for r in rows if r.reason == any_reason),
            key=lambda r: (_utc(r.received_at), r.evidence_id),
        )
        if any_enabled and anomalies:
            first = anomalies[0]
            signals.append(
                _make_signal(
                    rules,
                    learner_id,
                    signal_type,
                    any_rule_id,
                    [f"evidence:{first.evidence_id}"],
                    first.received_at,
                    first.received_at,
                )
            )
    return signals


def detect_replay(rules: AbuseRules, evidence: Iterable[EvidenceFact]) -> list[Signal]:
    """Many distinct redelivered event_ids in a window, or any event_id reuse conflict.

    One redelivery is normal at-least-once behaviour and never flags. Counting
    is by distinct event_id because the evidence index collapses identical
    redeliveries of the same event into one row.
    """
    cfg = rules.replay
    return _sorted_signals(
        _evidence_signals(
            rules,
            evidence,
            "replay",
            "duplicate_event_id",
            "replay.distinct_redelivery",
            cfg.window_minutes,
            cfg.min_distinct_replayed_events,
            "conflicting_event_id_reuse",
            "replay.conflicting_reuse",
            cfg.flag_conflicting_event_id_reuse,
        )
    )


def detect_duplicate_exploit(
    rules: AbuseRules, evidence: Iterable[EvidenceFact]
) -> list[Signal]:
    """A completion re-sent under many new event_ids, or a reward field in a source event.

    Counts distinct resubmission event_ids (each is a fresh delivery of an
    already accepted completion).
    """
    cfg = rules.duplicate_exploit
    return _sorted_signals(
        _evidence_signals(
            rules,
            evidence,
            "duplicate_exploit",
            "duplicate_completion_id",
            "duplicate_exploit.resubmitted_completion",
            cfg.window_minutes,
            cfg.min_distinct_resubmitted_completions,
            "reward_field_in_source_event",
            "duplicate_exploit.reward_field",
            cfg.flag_reward_field_in_source_event,
        )
    )


def detect_impossible_timing(
    rules: AbuseRules, completions: Iterable[CompletionFact]
) -> list[Signal]:
    """Repeated sub-minimum gaps inside a short window, or a completion burst.

    Timing is inferred from producer-claimed occurred_at values.
    """
    cfg = rules.impossible_timing
    signals: list[Signal] = []
    for learner_id, rows in _group(completions, lambda c: c.learner_id).items():
        ordered = _by_time(rows)
        short = [
            (earlier, later)
            for earlier, later in zip(ordered, ordered[1:])
            if (_utc(later.occurred_at) - _utc(earlier.occurred_at)).total_seconds()
            < cfg.min_gap_for(later.completion_kind)
        ]
        window = timedelta(minutes=cfg.short_gap_window_minutes)
        for start in range(len(short) - cfg.short_gaps_required + 1):
            group = short[start : start + cfg.short_gaps_required]
            first, last = group[0][0], group[-1][1]
            if _utc(last.occurred_at) - _utc(first.occurred_at) <= window:
                signals.append(
                    _make_signal(
                        rules,
                        learner_id,
                        "impossible_timing",
                        "impossible_timing.short_gaps",
                        _completion_refs(c for pair in group for c in pair),
                        first.occurred_at,
                        last.occurred_at,
                    )
                )
                break
        burst_window = timedelta(minutes=cfg.burst.window_minutes)
        for start in range(len(ordered) - cfg.burst.max_completions):
            group_c = ordered[start : start + cfg.burst.max_completions + 1]
            if _utc(group_c[-1].occurred_at) - _utc(group_c[0].occurred_at) <= burst_window:
                signals.append(
                    _make_signal(
                        rules,
                        learner_id,
                        "impossible_timing",
                        "impossible_timing.burst",
                        _completion_refs(group_c),
                        group_c[0].occurred_at,
                        group_c[-1].occurred_at,
                    )
                )
                break
    return _sorted_signals(signals)


def detect_scripted_farming(
    rules: AbuseRules, completions: Iterable[CompletionFact]
) -> list[Signal]:
    """Many repeats of already-completed lessons, or a machine-regular cadence.

    Reviews and remediation are excluded from both checks.
    """
    cfg = rules.scripted_farming
    signals: list[Signal] = []
    eligible = [c for c in completions if c.completion_kind in LESSON_KINDS]
    for learner_id, rows in _group(eligible, lambda c: c.learner_id).items():
        ordered = _by_time(rows)

        repeats = [c for c in ordered if c.repeat_index is not None and c.repeat_index >= 1]
        window = timedelta(hours=cfg.repeat.window_hours)
        need = cfg.repeat.min_repeat_completions
        for start in range(len(repeats) - need + 1):
            group = repeats[start : start + need]
            if _utc(group[-1].occurred_at) - _utc(group[0].occurred_at) <= window:
                signals.append(
                    _make_signal(
                        rules,
                        learner_id,
                        "scripted_farming",
                        "scripted_farming.repeat",
                        _completion_refs(group),
                        group[0].occurred_at,
                        group[-1].occurred_at,
                    )
                )
                break

        size = cfg.metronome.min_events
        for start in range(len(ordered) - size + 1):
            group = ordered[start : start + size]
            gaps = [
                (_utc(b.occurred_at) - _utc(a.occurred_at)).total_seconds()
                for a, b in zip(group, group[1:])
            ]
            mean = statistics.fmean(gaps)
            if mean < cfg.metronome.min_mean_gap_seconds:
                continue
            if statistics.pstdev(gaps) / mean <= cfg.metronome.max_cv:
                signals.append(
                    _make_signal(
                        rules,
                        learner_id,
                        "scripted_farming",
                        "scripted_farming.metronome",
                        _completion_refs(group),
                        group[0].occurred_at,
                        group[-1].occurred_at,
                    )
                )
                break
    return _sorted_signals(signals)


def detect_receive_timing(
    rules: AbuseRules, completions: Iterable[CompletionFact]
) -> list[Signal]:
    """Timing rules on the trusted receive time instead of the producer-claimed one.

    ``receive_burst``: more than ``max_completions`` completions received inside the
    window. ``future_dated``: at least ``min_completions`` completions claiming an
    occurred_at strictly later than received_at plus the tolerance. Each rule is off
    when its section is None (v1); completions without a received_at are ignored.
    """
    burst, future = rules.receive_burst, rules.future_dated
    signals: list[Signal] = []
    received = [c for c in completions if c.received_at is not None]
    for learner_id, rows in _group(received, lambda c: c.learner_id).items():
        if burst is not None:
            ordered = sorted(rows, key=lambda c: (_utc(c.received_at), c.completion_id))
            size = burst.max_completions + 1
            window = timedelta(minutes=burst.window_minutes)
            for start in range(len(ordered) - size + 1):
                group = ordered[start : start + size]
                if _utc(group[-1].received_at) - _utc(group[0].received_at) <= window:
                    signals.append(
                        _make_signal(
                            rules,
                            learner_id,
                            "impossible_timing",
                            "impossible_timing.receive_burst",
                            _completion_refs(group),
                            group[0].received_at,
                            group[-1].received_at,
                        )
                    )
                    break
        if future is not None:
            tolerance = timedelta(minutes=future.tolerance_minutes)
            ahead = _by_time(
                c for c in rows if _utc(c.occurred_at) > _utc(c.received_at) + tolerance
            )
            if len(ahead) >= future.min_completions:
                group = ahead[: future.min_completions]
                signals.append(
                    _make_signal(
                        rules,
                        learner_id,
                        "impossible_timing",
                        "impossible_timing.future_dated",
                        _completion_refs(group),
                        group[0].occurred_at,
                        group[-1].occurred_at,
                    )
                )
    return _sorted_signals(signals)


EXEMPT_KINDS = frozenset({"review", "remediation"})


def detect_review_misuse(
    rules: AbuseRules, completions: Iterable[CompletionFact]
) -> list[Signal]:
    """Flags abuse of the review/remediation exemption from decay and farming rules.

    ``orphan_review``: exempt completions of lessons never completed as a lesson
    beforehand, across at least ``orphan_min_lessons`` lessons.
    ``review_repeat``: more than ``max_reviews_per_lesson`` exempt completions of
    one lesson inside ``window_hours``. Off when the rule set has no
    ``review_misuse`` section (v1).
    """
    cfg = rules.review_misuse
    if cfg is None:
        return []
    signals: list[Signal] = []
    for learner_id, rows in _group(completions, lambda c: c.learner_id).items():
        ordered = _by_time(rows)

        first_lesson: dict[str, int] = {}
        for index, row in enumerate(ordered):
            if row.completion_kind in LESSON_KINDS:
                first_lesson.setdefault(row.lesson_id, index)
        orphans: dict[str, CompletionFact] = {}
        for index, row in enumerate(ordered):
            if row.completion_kind in EXEMPT_KINDS and not (
                row.lesson_id in first_lesson and first_lesson[row.lesson_id] < index
            ):
                orphans.setdefault(row.lesson_id, row)
        if len(orphans) >= cfg.orphan_min_lessons:
            group = sorted(
                orphans.values(), key=lambda c: (_utc(c.occurred_at), c.completion_id)
            )[: cfg.orphan_min_lessons]
            signals.append(
                _make_signal(
                    rules,
                    learner_id,
                    "scripted_farming",
                    "scripted_farming.orphan_review",
                    _completion_refs(group),
                    group[0].occurred_at,
                    group[-1].occurred_at,
                )
            )

        window = timedelta(hours=cfg.window_hours)
        size = cfg.max_reviews_per_lesson + 1
        for lesson_rows in _group(
            [r for r in ordered if r.completion_kind in EXEMPT_KINDS], lambda c: c.lesson_id
        ).values():
            for start in range(len(lesson_rows) - size + 1):
                group = lesson_rows[start : start + size]
                if _utc(group[-1].occurred_at) - _utc(group[0].occurred_at) <= window:
                    signals.append(
                        _make_signal(
                            rules,
                            learner_id,
                            "scripted_farming",
                            "scripted_farming.review_repeat",
                            _completion_refs(group),
                            group[0].occurred_at,
                            group[-1].occurred_at,
                        )
                    )
                    break
    return _sorted_signals(signals)


def detect_all(
    rules: AbuseRules,
    completions: Iterable[CompletionFact],
    evidence: Iterable[EvidenceFact],
) -> list[Signal]:
    """Runs every detector; the result is sorted and independent of input order."""
    completions = list(completions)
    evidence = list(evidence)
    return _sorted_signals(
        [
            *detect_replay(rules, evidence),
            *detect_duplicate_exploit(rules, evidence),
            *detect_impossible_timing(rules, completions),
            *detect_scripted_farming(rules, completions),
            *detect_review_misuse(rules, completions),
            *detect_receive_timing(rules, completions),
        ]
    )
