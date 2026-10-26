"""Seeded labeled personas that drive the REAL ingestion path (injected clock, fixed seeds).

Gap ranges follow the M6 brief; where the brief's numbers cannot all hold at once
(six lessons in ten minutes with 90-180 s gaps) the gaps win and the persona
simply runs a little over ten minutes.
"""

from datetime import timedelta
import random

from src.abuse.evaluation import ATTACKER, BENIGN, BORDERLINE, LabeledLearner
from tests.abuse.harness import EPOCH, Harness

SEED = 20261026
LESSON = "lesson"
REVIEW = "review"


def _label(h, persona, category, truth=(), expected_detected=None, note=""):
    learner_id = h.new_learner()
    return learner_id, LabeledLearner(
        learner_id,
        persona,
        category,
        frozenset(truth),
        None if expected_detected is None else frozenset(expected_detected),
        note,
    )


def _run(h, learner_id, start, gaps, lessons, **kwargs):
    """Completes lessons[i] at start + sum(gaps[:i]); returns the accepted event ids."""
    t, events = start, []
    for i, lesson_id in enumerate(lessons):
        if i:
            t += timedelta(seconds=gaps[i - 1])
        event_id = h.next_event_id()
        assert h.complete(learner_id, t, lesson_id=lesson_id, event_id=event_id, **kwargs).status == "accepted"
        events.append((event_id, t))
    return events


def steady_learner(h, rng, start):
    lid, label = _label(h, "steady_learner", BENIGN)
    gaps = [rng.uniform(180, 1200) for _ in range(19)]
    _run(h, lid, start, gaps, [f"steady-{i}" for i in range(20)])
    return label


def bursty_human(h, rng, start):
    lid, label = _label(h, "bursty_but_human", BENIGN)
    gaps = [rng.uniform(90, 180) for _ in range(5)]
    _run(h, lid, start, gaps, [f"burst-{i}" for i in range(6)])
    return label


def spaced_repetition_reviewer(h, rng, start):
    lid, label = _label(h, "spaced_repetition_reviewer", BENIGN)
    lessons = [f"sr-{i}" for i in range(5)]
    _run(h, lid, start, [rng.uniform(300, 900) for _ in range(4)], lessons)
    for day in (1, 3, 7):  # the same lessons come back as reviews
        _run(h, lid, start + timedelta(days=day), [rng.uniform(120, 600) for _ in range(4)], lessons, kind=REVIEW)
    # a fast 12 s review gap (reviews have a 10 s floor)
    _run(h, lid, start + timedelta(days=9), [12, 12, 12], lessons[:4], kind=REVIEW)
    return label


def flaky_network_learner(h, rng, start):
    lid, label = _label(h, "flaky_network_learner", BENIGN)
    events = _run(h, lid, start, [rng.uniform(300, 900) for _ in range(7)], [f"flaky-{i}" for i in range(8)])
    event_id, t = events[3]
    lesson = "flaky-3"
    h.deliver(h.envelope(lid, _completion_of(h, lid, event_id), event_id, t, lesson_id=lesson),
              events[-1][1] + timedelta(minutes=1))  # one redelivery
    late = start + timedelta(hours=1, minutes=3)  # delivered 30 h after it happened, inside 72 h
    h.complete(lid, late, lesson_id="flaky-late", received_at=late + timedelta(hours=30))
    return label


def _completion_of(h, learner_id, event_id):
    for completion_id, source in h.learner(learner_id).completion_sources.items():
        if source == event_id:
            return completion_id
    raise AssertionError(f"event {event_id} was not accepted for {learner_id}")


def fast_completer(h, rng, start):
    lid, label = _label(h, "fast_completer", ATTACKER, {"impossible_timing"})
    _run(h, lid, start, [5] * 7, [f"fast-{i}" for i in range(8)])
    return label


def metronomic_bot(h, rng, start):
    lid, label = _label(h, "metronomic_bot", ATTACKER, {"scripted_farming"})
    _run(h, lid, start, [30] * 14, [f"metro-{i}" for i in range(15)])
    return label


def lesson_farmer(h, rng, start):
    lid, label = _label(h, "lesson_farmer", ATTACKER, {"scripted_farming"})
    _run(h, lid, start, [rng.uniform(1200, 3000) for _ in range(14)], ["farm-lesson"] * 15, kind=LESSON)
    return label


def replay_attacker(h, rng, start):
    lid, label = _label(h, "replay_attacker", ATTACKER, {"replay"})
    events = _run(h, lid, start, [rng.uniform(300, 900) for _ in range(7)], [f"replay-{i}" for i in range(8)])
    resend = events[-1][1] + timedelta(minutes=2)
    for i, (event_id, t) in enumerate(events):
        h.deliver(h.envelope(lid, _completion_of(h, lid, event_id), event_id, t, lesson_id=f"replay-{i}"),
                  resend + timedelta(seconds=10 * i))
    return label


def duplicate_exploiter(h, rng, start):
    lid, label = _label(h, "duplicate_exploiter", ATTACKER, {"duplicate_exploit"})
    events = _run(h, lid, start, [rng.uniform(300, 900) for _ in range(2)], [f"dup-{i}" for i in range(3)])
    completion_id = _completion_of(h, lid, events[0][0])
    resend = events[-1][1] + timedelta(minutes=2)
    for i in range(4):  # same completion_id under four brand-new event_ids
        result = h.deliver(
            h.envelope(lid, completion_id, h.next_event_id(), events[0][1], lesson_id="dup-0"),
            resend + timedelta(seconds=15 * i),
        )
        assert (result.status, result.reason) == ("duplicate", "duplicate_completion_id")
    return label


def reward_field_injector(h, rng, start):
    lid, label = _label(h, "reward_field_injector", ATTACKER, {"duplicate_exploit"})
    events = _run(h, lid, start, [rng.uniform(300, 900) for _ in range(2)], [f"inject-{i}" for i in range(3)])
    t = events[-1][1] + timedelta(minutes=10)
    envelope = h.envelope(lid, h.next_completion_id(), h.next_event_id(), t, lesson_id="inject-x",
                          payload_extra={"xp_amount": 9999})
    assert h.deliver(envelope).reason == "reward_field_in_source_event"
    return label


def review_label_spoofer(h, rng, start):
    lid, label = _label(h, "review_label_spoofer", ATTACKER, {"scripted_farming"})
    _run(h, lid, start, [rng.uniform(40, 200) for _ in range(14)], ["spoof-lesson"] * 15, kind=REVIEW)
    return label


def orphan_reviewer(h, rng, start):
    lid, label = _label(h, "orphan_reviewer", ATTACKER, {"scripted_farming"})
    _run(h, lid, start, [rng.uniform(300, 900) for _ in range(4)], [f"orphan-{i}" for i in range(5)], kind=REVIEW)
    return label


def timestamp_spoofer(h, rng, start):
    """Claims human-looking gaps but delivers everything within two minutes, a day later."""
    lid, label = _label(h, "timestamp_spoofer", ATTACKER, {"impossible_timing"})
    claimed = start
    delivery = start + timedelta(hours=30)
    for i in range(60):
        result = h.complete(lid, claimed, lesson_id=f"ts-{i}", received_at=delivery + timedelta(seconds=2 * i))
        assert result.status == "accepted"
        claimed += timedelta(minutes=rng.uniform(3, 9))
    return label


def honest_offline_sync(h, rng, start):
    """An offline client syncing a backlog: claimed 5 minutes apart, all received within a minute."""
    lid, label = _label(h, "honest_offline_sync", BENIGN)
    delivery = start + timedelta(hours=3)
    for i in range(10):
        result = h.complete(lid, start + timedelta(minutes=5 * i), lesson_id=f"offline-{i}",
                            received_at=delivery + timedelta(seconds=5 * i))
        assert result.status == "accepted"
    return label


def slow_jittered_bot(h, rng, start):
    lid, label = _label(
        h, "slow_bot_with_jitter", BORDERLINE, {"scripted_farming"}, set(),
        "expected miss: roughly two-minute cadence with 60-180 s random jitter, distinct lessons",
    )
    _run(h, lid, start, [rng.uniform(60, 180) for _ in range(29)], [f"slow-{i}" for i in range(30)])
    return label


def perfectly_regular_human(h, rng, start):
    lid, label = _label(
        h, "perfectly_regular_human", BORDERLINE, set(), {"scripted_farming"},
        "expected false positive: a human on a fixed 10-minute schedule looks like a metronome",
    )
    _run(h, lid, start, [600] * 13, [f"regular-{i}" for i in range(14)])
    return label


PERSONAS = (
    steady_learner,
    bursty_human,
    spaced_repetition_reviewer,
    flaky_network_learner,
    fast_completer,
    metronomic_bot,
    lesson_farmer,
    replay_attacker,
    duplicate_exploiter,
    reward_field_injector,
    review_label_spoofer,
    orphan_reviewer,
    timestamp_spoofer,
    honest_offline_sync,
    slow_jittered_bot,
    perfectly_regular_human,
)


def build_population(h: Harness, seed: int = SEED, copies: int = 1) -> list[LabeledLearner]:
    """Runs every persona (``copies`` times each) through real ingestion; returns the labels."""
    rng = random.Random(seed)
    labels = []
    for copy in range(copies):
        for index, persona in enumerate(PERSONAS):
            start = EPOCH + timedelta(days=2 * copy, minutes=3 * index)
            labels.append(persona(h, random.Random(rng.random()), start))
    return labels
