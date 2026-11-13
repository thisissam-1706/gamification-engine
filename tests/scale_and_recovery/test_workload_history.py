"""Workload ids and prior-week history (M3 cohort wiring).

Lesson ids are "<module_id>/<lesson>"; the history lessons fall only on Monday to Thursday of the
ISO week before the epoch and never change an event in the epoch week or later.
"""

from datetime import datetime

from src.leaderboard.cohorts import get_calendar_week
from src.scale_and_recovery.rebuild import FullStateRebuildHarness
from src.scale_and_recovery.workload import (
    HISTORY_LESSON_COUNTS,
    MODULE_IDS,
    WorkloadProfile,
    generate_synthetic_workload,
)

PROFILE = WorkloadProfile(population_size=15, events_per_learner=12, seed=42, epoch="2026-W42")


def _when(event) -> datetime:
    return datetime.fromisoformat(event.occurred_at.replace("Z", "+00:00"))


def test_lesson_ids_have_a_module_prefix_and_completion_ids_stay_unique():
    w = generate_synthetic_workload(PROFILE)

    for event in w.events + w.history_events:
        module, separator, lesson = event.lesson_id.partition("/")
        assert separator == "/" and lesson, event.lesson_id
        assert module == w.learner_modules[event.learner_id]
    assert {w.learner_modules[l] for l in w.learner_ids} == set(MODULE_IDS)
    assert any("/lesson_late_" in e.lesson_id for e in w.events if e.is_late)
    assert any("/lesson_1" == e.lesson_id[-9:] for e in w.events)
    originals = [e.completion_id for e in w.events if not e.is_duplicate] + [
        e.completion_id for e in w.history_events
    ]
    assert len(originals) == len(set(originals))
    for e in w.events:  # a duplicate keeps its original's lesson id
        if e.is_duplicate:
            twin = next(o for o in w.events if o.completion_id == e.completion_id and not o.is_duplicate)
            assert e.lesson_id == twin.lesson_id


def test_history_is_only_monday_to_thursday_of_the_week_before_the_epoch():
    w = generate_synthetic_workload(PROFILE)

    assert w.history_events
    for event in w.history_events:
        assert get_calendar_week(_when(event)) == "2026-W41"
        assert _when(event).isoweekday() <= 4  # Monday to Thursday
    assert all(get_calendar_week(_when(e)) >= "2026-W41" for e in w.events)
    # History is separate from the main stream and its counts.
    assert w.total_events == len(w.events)
    assert not {e.completion_id for e in w.history_events} & {e.completion_id for e in w.events}


def test_history_counts_follow_the_pattern_and_cover_every_tier():
    w = generate_synthetic_workload(PROFILE)

    counts = [
        sum(1 for e in w.history_events if e.learner_id == lid) for lid in w.learner_ids
    ]
    assert counts == [HISTORY_LESSON_COUNTS[i % 10] for i in range(15)]
    assert min(counts) < 3 <= max(c for c in counts if c < 10) and max(counts) >= 10


def test_workload_and_its_history_are_deterministic():
    first = generate_synthetic_workload(PROFILE)
    second = generate_synthetic_workload(PROFILE)

    assert first.events == second.events
    assert first.history_events == second.history_events
    assert first.learner_modules == second.learner_modules
    assert first.cohort_assignments == {} == second.cohort_assignments  # filled only when ingested


def test_ingesting_the_workload_builds_the_same_cohorts_every_run(tmp_path):
    def run(name):
        workload = generate_synthetic_workload(PROFILE)
        harness = FullStateRebuildHarness(
            str(tmp_path / f"{name}_e.db"), str(tmp_path / f"{name}_l.db"), epoch="2026-W42"
        )
        assert harness.run_rebuild_verification(workload).bit_identical
        return workload.cohort_assignments

    first, second = run("a"), run("b")

    assert first == second
    assert set(first) == {f"learner_{i:04d}" for i in range(15)}
    assert 1 < len(set(first.values())) <= 9  # tier and module separate the learners
