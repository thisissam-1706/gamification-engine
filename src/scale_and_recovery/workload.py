"""Deterministic synthetic workload generator for scale, replay, and latency benchmarks."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import random
from typing import Any


@dataclass(frozen=True)
class WorkloadProfile:
    """Configuration for a pinned synthetic scale and recovery run."""

    population_size: int = 50
    cohort_count: int = 2
    events_per_learner: int = 30
    duplicate_rate: float = 0.05
    late_rate: float = 0.05
    seed: int = 42
    epoch: str = "2026-W42"
    base_start_iso: str = "2026-10-12T08:00:00Z"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SyntheticEvent:
    """A synthetic learner activity event with deterministic metadata."""

    sequence_num: int
    learner_id: str
    cohort_id: str
    completion_id: str
    lesson_id: str
    occurred_at: str
    activity_date: str
    is_duplicate: bool = False
    is_late: bool = False
    original_completion_id: str | None = None


@dataclass
class SyntheticWorkload:
    """The generated synthetic dataset pinned by seed and parameters."""

    profile: WorkloadProfile
    learner_ids: list[str]
    cohort_assignments: dict[str, str]
    events: list[SyntheticEvent]
    total_events: int
    clean_events_count: int
    duplicate_events_count: int
    late_events_count: int


def generate_synthetic_workload(profile: WorkloadProfile) -> SyntheticWorkload:
    """Deterministically generates a synthetic event stream conforming to the profile.
    
    Uses pure PRNG with pinned seed and deterministic time math (never wall-clock).
    """
    rng = random.Random(profile.seed)
    base_dt = datetime.fromisoformat(profile.base_start_iso.replace("Z", "+00:00")).astimezone(timezone.utc)

    # 1. Deterministic population and cohort assignments
    learner_ids = [f"learner_{i:04d}" for i in range(profile.population_size)]
    cohort_names = [f"cohort_{chr(ord('a') + c)}" for c in range(profile.cohort_count)]
    cohort_assignments = {
        lid: cohort_names[i % profile.cohort_count] for i, lid in enumerate(learner_ids)
    }

    # 2. Build interleaving event sequence
    clean_events: list[SyntheticEvent] = []
    learner_last_time: dict[str, datetime] = {lid: base_dt for lid in learner_ids}
    learner_completions: dict[str, list[SyntheticEvent]] = {lid: [] for lid in learner_ids}

    seq = 0
    # Create baseline events across learners
    for step in range(profile.events_per_learner):
        for lid in learner_ids:
            cohort_id = cohort_assignments[lid]
            # Time progression: each step advances hours/days deterministically
            prev_time = learner_last_time[lid]
            # Advance between 2 to 24 hours
            advance_hours = 4 + (rng.randint(0, 16))
            event_dt = prev_time + timedelta(hours=advance_hours)
            learner_last_time[lid] = event_dt

            comp_id = f"cmp_{lid}_{step:03d}"
            lesson_id = f"lesson_{(step % 10) + 1}"
            iso_time = event_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            act_date = event_dt.strftime("%Y-%m-%d")

            event = SyntheticEvent(
                sequence_num=seq,
                learner_id=lid,
                cohort_id=cohort_id,
                completion_id=comp_id,
                lesson_id=lesson_id,
                occurred_at=iso_time,
                activity_date=act_date,
                is_duplicate=False,
                is_late=False,
            )
            clean_events.append(event)
            learner_completions[lid].append(event)
            seq += 1

    # 3. Inject deterministic duplicate mix
    num_duplicates = int(len(clean_events) * profile.duplicate_rate)
    duplicates: list[SyntheticEvent] = []
    if clean_events and num_duplicates > 0:
        candidates = list(clean_events)
        rng.shuffle(candidates)
        for i in range(num_duplicates):
            orig = candidates[i % len(candidates)]
            dup_dt = datetime.fromisoformat(orig.occurred_at.replace("Z", "+00:00")) + timedelta(minutes=5 + i)
            dup_event = SyntheticEvent(
                sequence_num=seq,
                learner_id=orig.learner_id,
                cohort_id=orig.cohort_id,
                completion_id=orig.completion_id,  # identical business completion_id!
                lesson_id=orig.lesson_id,
                occurred_at=dup_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                activity_date=orig.activity_date,
                is_duplicate=True,
                is_late=False,
                original_completion_id=orig.completion_id,
            )
            duplicates.append(dup_event)
            seq += 1

    # 4. Inject deterministic late / out-of-order mix
    # Late events have occurred_at within 72h before latest learner event
    num_late = int(len(clean_events) * profile.late_rate)
    late_events: list[SyntheticEvent] = []
    if clean_events and num_late > 0:
        candidates = [e for e in clean_events if not e.is_duplicate]
        rng.shuffle(candidates)
        for i in range(num_late):
            target = candidates[i % len(candidates)]
            target_dt = datetime.fromisoformat(target.occurred_at.replace("Z", "+00:00"))
            # 12 to 36 hours earlier (within 72h window)
            offset_hours = 12 + (i % 24)
            late_dt = target_dt - timedelta(hours=offset_hours)
            late_comp_id = f"cmp_late_{target.learner_id}_{i:03d}"
            late_event = SyntheticEvent(
                sequence_num=seq,
                learner_id=target.learner_id,
                cohort_id=target.cohort_id,
                completion_id=late_comp_id,
                lesson_id=f"lesson_late_{(i % 5) + 1}",
                occurred_at=late_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                activity_date=late_dt.strftime("%Y-%m-%d"),
                is_duplicate=False,
                is_late=True,
            )
            late_events.append(late_event)
            seq += 1

    # Combine clean, duplicates, and late events
    # Interleave duplicates and late events deterministically into the stream
    combined = list(clean_events) + duplicates + late_events
    # Sort deterministically by sequence_num or stable interleave
    # We want a realistic ingestion order: clean events arrive chronologically,
    # with late events and duplicates injected throughout
    rng.shuffle(duplicates)
    rng.shuffle(late_events)

    final_stream: list[SyntheticEvent] = []
    dup_idx = 0
    late_idx = 0
    for i, evt in enumerate(clean_events):
        final_stream.append(evt)
        # Inject duplicate roughly according to rate
        if dup_idx < len(duplicates) and (i % max(1, int(1 / max(profile.duplicate_rate, 0.001)))) == 0:
            final_stream.append(duplicates[dup_idx])
            dup_idx += 1
        # Inject late event roughly according to rate
        if late_idx < len(late_events) and (i % max(1, int(1 / max(profile.late_rate, 0.001)))) == 1:
            final_stream.append(late_events[late_idx])
            late_idx += 1

    # Append any remaining
    while dup_idx < len(duplicates):
        final_stream.append(duplicates[dup_idx])
        dup_idx += 1
    while late_idx < len(late_events):
        final_stream.append(late_events[late_idx])
        late_idx += 1

    # Re-number sequence numbers sequentially
    for idx, evt in enumerate(final_stream):
        object.__setattr__(evt, "sequence_num", idx)

    return SyntheticWorkload(
        profile=profile,
        learner_ids=learner_ids,
        cohort_assignments=cohort_assignments,
        events=final_stream,
        total_events=len(final_stream),
        clean_events_count=len(clean_events),
        duplicate_events_count=len(duplicates),
        late_events_count=len(late_events),
    )
