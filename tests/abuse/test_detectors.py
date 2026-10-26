"""M6 pure detectors: thresholds, near-misses, determinism, evidence."""

import json
import random
import statistics
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from importlib.resources import files

import pytest
from jsonschema import Draft7Validator

from src.abuse.detectors import (
    CompletionFact,
    EvidenceFact,
    Signal,
    detect_all,
    detect_duplicate_exploit,
    detect_impossible_timing,
    detect_replay,
    detect_scripted_farming,
)
from src.abuse.rules import get_abuse_rules

RULES = get_abuse_rules("v1")
T0 = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
SCHEMA = Draft7Validator(
    json.loads((files("src") / "schemas" / "events" / "abuse_signal_raised.schema.json").read_text())
)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def comp(i, seconds, *, learner="L1", lesson=None, kind="lesson", repeat=0):
    return CompletionFact(
        learner_id=learner,
        completion_id=f"c{i:03d}",
        event_id=f"e{i:03d}",
        lesson_id=lesson or f"lesson-{i}",
        completion_kind=kind,
        occurred_at=at(seconds),
        repeat_index=repeat,
        xp_amount=100,
    )


def ev(i, seconds, reason, *, learner="L1", event=None, action=None):
    return EvidenceFact(
        learner_id=learner,
        reason=reason,
        action_id=action if action is not None else f"c-orig-{i:03d}",
        event_id=event if event is not None else f"dup-{i:03d}",
        original_ref=f"e-orig-{i:03d}",
        received_at=at(seconds),
        evidence_id=i,
    )


def check_signals(signals, completions=(), evidence=()):
    """Shared invariants: schema-valid, suspected, non-empty resolvable evidence."""
    refs = {f"event:{c.event_id}" for c in completions}
    refs |= {f"completion:{c.completion_id}" for c in completions}
    refs |= {f"evidence:{e.evidence_id}" for e in evidence}
    for signal in signals:
        assert signal.status == "suspected"
        assert signal.evidence_refs
        assert list(signal.evidence_refs) == sorted(set(signal.evidence_refs))
        assert set(signal.evidence_refs) <= refs
        assert signal.rule_version == "v1" and signal.rule_digest == RULES.digest
        SCHEMA.validate(signal.to_payload())
        assert set(signal.to_payload()) == {"signal_id", "signal_type", "status", "evidence_refs"}
        assert signal.signal_id.startswith(f"abuse:{signal.rule_id}:{RULES.digest[:12]}:{signal.learner_id}:")
    return signals


# -- replay -------------------------------------------------------------------


def replays(n, spacing_s, start=0):
    return [ev(i, start + i * spacing_s, "duplicate_event_id") for i in range(n)]


def test_replay_single_redelivery_never_flags():
    assert detect_replay(RULES, replays(1, 1)) == []
    assert detect_replay(RULES, replays(4, 1)) == []


def test_replay_flags_at_threshold_and_above():
    for n in (5, 6):
        facts = replays(n, 10)
        [signal] = check_signals(detect_replay(RULES, facts), evidence=facts)
        assert signal.signal_type == "replay"
        assert signal.rule_id == "replay.distinct_redelivery"
        assert len(signal.evidence_refs) == 5


def test_replay_window_boundary_is_inclusive():
    inside = replays(5, 15 * 60)  # 0..60 min
    outside = replays(5, 15 * 60 + 1)  # span 60 min + 4 s
    assert len(detect_replay(RULES, inside)) == 1
    assert detect_replay(RULES, outside) == []


def test_replay_counts_distinct_event_ids_not_rows():
    rows = [ev(i, i, "duplicate_event_id", event="same-event") for i in range(8)]
    assert detect_replay(RULES, rows) == []


def test_replay_conflicting_reuse_flags_once_with_one_row():
    facts = [ev(7, 5, "conflicting_event_id_reuse"), ev(3, 1, "conflicting_event_id_reuse")]
    [signal] = check_signals(detect_replay(RULES, facts), evidence=facts)
    assert signal.rule_id == "replay.conflicting_reuse"
    assert signal.evidence_refs == ("evidence:3",)


def test_replay_conflicting_reuse_can_be_disabled():
    rules = replace(RULES, replay=replace(RULES.replay, flag_conflicting_event_id_reuse=False))
    assert detect_replay(rules, [ev(1, 0, "conflicting_event_id_reuse")]) == []


def test_replay_ignores_other_reasons_and_isolates_learners():
    facts = [ev(i, i, "duplicate_completion_id") for i in range(9)]
    facts += [ev(100 + i, i, "duplicate_event_id", learner=f"L{i}") for i in range(9)]
    assert detect_replay(RULES, facts) == []


# -- duplicate_exploit ---------------------------------------------------------


def resubmits(n, spacing_s):
    return [ev(i, i * spacing_s, "duplicate_completion_id", action="c-same") for i in range(n)]


def test_duplicate_exploit_boundary():
    assert detect_duplicate_exploit(RULES, resubmits(2, 1)) == []
    facts = resubmits(3, 1)
    [signal] = check_signals(detect_duplicate_exploit(RULES, facts), evidence=facts)
    assert signal.signal_type == "duplicate_exploit"
    assert signal.rule_id == "duplicate_exploit.resubmitted_completion"
    assert len(signal.evidence_refs) == 3
    assert len(detect_duplicate_exploit(RULES, resubmits(4, 1))) == 1


def test_duplicate_exploit_window_boundary():
    assert len(detect_duplicate_exploit(RULES, resubmits(3, 30 * 60))) == 1
    assert detect_duplicate_exploit(RULES, resubmits(3, 30 * 60 + 1)) == []


def test_duplicate_exploit_same_new_event_id_counts_once():
    rows = [ev(i, i, "duplicate_completion_id", action="c", event="new-1") for i in range(6)]
    assert detect_duplicate_exploit(RULES, rows) == []


def test_reward_field_injection_flags_on_one_row():
    facts = [ev(2, 0, "reward_field_in_source_event"), ev(1, 5, "reward_field_in_source_event")]
    [signal] = check_signals(detect_duplicate_exploit(RULES, facts), evidence=facts)
    assert signal.rule_id == "duplicate_exploit.reward_field"
    assert signal.evidence_refs == ("evidence:2",)  # earliest by received_at
    rules = replace(
        RULES,
        duplicate_exploit=replace(RULES.duplicate_exploit, flag_reward_field_in_source_event=False),
    )
    assert detect_duplicate_exploit(rules, facts) == []


def test_other_evidence_reasons_never_flag_duplicate_exploit():
    facts = [ev(i, i, reason) for i, reason in enumerate(
        ["untrusted_producer", "schema_invalid", "outside_late_window", "dedup_key_mismatch", "quest_already_completed"]
    )]
    assert detect_duplicate_exploit(RULES, facts) == []
    assert detect_replay(RULES, facts) == []


# -- impossible_timing ---------------------------------------------------------


def run(gaps, *, kind="lesson", start=0.0, learner="L1"):
    """Completions with the given gaps (seconds) between consecutive ones."""
    t, out = start, [comp(0, start, kind=kind, learner=learner)]
    for i, gap in enumerate(gaps, 1):
        t += gap
        out.append(comp(i, t, kind=kind, learner=learner))
    return out


def test_timing_two_short_gaps_do_not_flag_three_do():
    assert detect_impossible_timing(RULES, run([5, 5])) == []
    facts = run([5, 5, 5])
    [signal] = check_signals(detect_impossible_timing(RULES, facts), facts)
    assert signal.signal_type == "impossible_timing"
    assert signal.rule_id == "impossible_timing.short_gaps"
    assert len(signal.evidence_refs) == 8  # 4 completions x (completion, event)


def test_timing_gap_equal_to_minimum_is_not_short():
    assert detect_impossible_timing(RULES, run([20, 20, 20, 20])) == []
    assert len(detect_impossible_timing(RULES, run([19, 19, 19]))) == 1


def test_timing_per_kind_minimum_uses_later_completion():
    assert detect_impossible_timing(RULES, run([12, 12, 12, 12], kind="review")) == []
    assert len(detect_impossible_timing(RULES, run([9, 9, 9], kind="review"))) == 1
    assert len(detect_impossible_timing(RULES, run([15, 15, 15], kind="remediation"))) == 1
    mixed = [comp(0, 0), comp(1, 12, kind="review"), comp(2, 24, kind="review"), comp(3, 36, kind="review")]
    assert detect_impossible_timing(RULES, mixed) == []


def test_timing_short_gaps_must_fall_inside_window():
    # three 5 s gaps, but separated by long human pauses
    facts = [comp(0, 0), comp(1, 5), comp(2, 3600), comp(3, 3605), comp(4, 7200), comp(5, 7205)]
    assert detect_impossible_timing(RULES, facts) == []
    # window edge: first completion to last completion of the third short gap == 10 min
    edge = [comp(0, 0), comp(1, 5), comp(2, 300), comp(3, 305), comp(4, 595), comp(5, 600)]
    assert len(detect_impossible_timing(RULES, edge)) == 1
    over = [comp(0, 0), comp(1, 5), comp(2, 300), comp(3, 305), comp(4, 596), comp(5, 601)]
    assert detect_impossible_timing(RULES, over) == []


def test_timing_identical_timestamps_are_zero_gaps():
    facts = [comp(i, 0) for i in range(4)]
    assert len(detect_impossible_timing(RULES, facts)) == 1


def test_timing_burst_boundary():
    twenty = run([30] * 19)  # 20 completions over 570 s
    assert detect_impossible_timing(RULES, twenty) == []
    facts = run([30] * 20)  # 21 completions spanning exactly 10 min
    [signal] = check_signals(detect_impossible_timing(RULES, facts), facts)
    assert signal.rule_id == "impossible_timing.burst"
    assert detect_impossible_timing(RULES, run([31] * 20)) == []  # spans 620 s


def test_timing_human_cadence_near_miss():
    assert detect_impossible_timing(RULES, run([180, 95, 150, 120, 90, 175])) == []


def test_timing_input_order_and_learners_are_independent():
    facts = run([5, 5, 5], learner="A") + run([60, 60, 60], learner="B")
    [signal] = detect_impossible_timing(RULES, facts)
    assert signal.learner_id == "A"


# -- scripted_farming -----------------------------------------------------------


def farm(n, spacing_s, *, kind="lesson", repeat_from=1, lesson="l1"):
    return [comp(i, i * spacing_s, lesson=lesson, kind=kind, repeat=repeat_from + i) for i in range(n)]


def test_farming_repeat_boundary():
    assert detect_scripted_farming(RULES, farm(9, 600)) == []
    facts = farm(10, 600)
    [signal] = check_signals(detect_scripted_farming(RULES, facts), facts)
    assert signal.signal_type == "scripted_farming"
    assert signal.rule_id == "scripted_farming.repeat"
    assert len(detect_scripted_farming(RULES, farm(11, 600))) == 1


def test_farming_repeat_window_boundary():
    inside = farm(10, 24 * 3600 // 9)  # span exactly 24 h
    assert [s.rule_id for s in detect_scripted_farming(RULES, inside)] == ["scripted_farming.repeat"]
    outside = farm(10, 24 * 3600 // 9 + 1)
    assert detect_scripted_farming(RULES, outside) == []


def test_farming_ignores_first_attempts_unknown_index_and_exempt_kinds():
    assert detect_scripted_farming(RULES, farm(30, 700, repeat_from=0)[:1]) == []
    assert detect_scripted_farming(RULES, farm(20, 700, kind="review")) == []
    assert detect_scripted_farming(RULES, farm(20, 700, kind="remediation")) == []
    # irregular spacing keeps the cadence rule out of this repeat-only check
    irregular = [replace(c, occurred_at=at(i * i * 40), repeat_index=None)
                 for i, c in enumerate(farm(20, 700))]
    assert detect_scripted_farming(RULES, irregular) == []


def test_farming_absent_kind_counts_as_lesson():
    facts = [replace(c, completion_kind=None) for c in farm(10, 600)]
    assert len(detect_scripted_farming(RULES, facts)) == 1


def metro(gaps, kind="lesson"):
    return run(gaps, kind=kind)


def test_metronome_boundary():
    assert detect_scripted_farming(RULES, metro([30] * 10)) == []  # 11 events
    facts = metro([30] * 11)  # 12 events
    [signal] = check_signals(detect_scripted_farming(RULES, facts), facts)
    assert signal.rule_id == "scripted_farming.metronome"
    assert len(detect_scripted_farming(RULES, metro([30] * 14))) == 1


def test_metronome_human_varied_cadence_does_not_flag():
    rng = random.Random(11)
    gaps = [rng.randint(180, 1200) for _ in range(20)]
    assert detect_scripted_farming(RULES, metro(gaps)) == []


def test_metronome_cv_threshold_is_inclusive():
    gaps = [30, 31] * 5 + [30]
    cv = statistics.pstdev(gaps) / statistics.mean(gaps)
    at_limit = replace(RULES, scripted_farming=replace(
        RULES.scripted_farming, metronome=replace(RULES.scripted_farming.metronome, max_cv=cv)))
    below = replace(RULES, scripted_farming=replace(
        RULES.scripted_farming, metronome=replace(RULES.scripted_farming.metronome, max_cv=cv * 0.99)))
    assert len(detect_scripted_farming(at_limit, metro(gaps))) == 1
    assert detect_scripted_farming(below, metro(gaps)) == []


def test_metronome_requires_minimum_mean_gap_and_excludes_reviews():
    assert detect_scripted_farming(RULES, [comp(i, 0) for i in range(15)]) == []
    assert detect_scripted_farming(RULES, metro([30] * 14, kind="review")) == []
    assert detect_scripted_farming(RULES, metro([30] * 14, kind="remediation")) == []


def test_metronome_ignores_reviews_interleaved_between_lessons():
    facts = []
    for i in range(14):
        facts.append(comp(i, i * 30))
    facts += [comp(100 + i, i * 30 + 7, kind="review") for i in range(14)]
    [signal] = detect_scripted_farming(RULES, facts)
    assert signal.rule_id == "scripted_farming.metronome"


# -- cross-cutting ----------------------------------------------------------------


def corpus():
    completions = run([5, 5, 5], learner="A") + [
        replace(c, completion_id=f"x{c.completion_id}", event_id=f"x{c.event_id}")
        for c in metro([30] * 14)
    ]
    completions += [
        replace(c, learner_id="B", completion_id=f"f{c.completion_id}", event_id=f"f{c.event_id}")
        for c in farm(12, 500)
    ]
    evidence = replays(6, 5) + resubmits(4, 5) + [ev(900, 0, "reward_field_in_source_event", learner="C")]
    return completions, evidence


def test_detect_all_is_deterministic_and_order_independent():
    completions, evidence = corpus()
    first = detect_all(RULES, completions, evidence)
    assert first == detect_all(RULES, completions, evidence)
    shuffled_c, shuffled_e = completions[:], evidence[:]
    random.Random(1).shuffle(shuffled_c)
    random.Random(2).shuffle(shuffled_e)
    assert detect_all(RULES, shuffled_c, shuffled_e) == first
    keys = [(s.learner_id, s.signal_type, s.rule_id, s.signal_id) for s in first]
    assert keys == sorted(keys)
    check_signals(first, completions, evidence)
    assert {s.signal_type for s in first} == {
        "replay", "impossible_timing", "scripted_farming", "duplicate_exploit"
    }


def test_signal_ids_are_unique_and_depend_on_evidence_and_rules():
    completions, evidence = corpus()
    signals = detect_all(RULES, completions, evidence)
    assert len({s.signal_id for s in signals}) == len(signals)
    other = replace(RULES, replay=replace(RULES.replay, window_minutes=61))
    other_ids = {s.signal_id for s in detect_all(other, completions, evidence)}
    assert other_ids != {s.signal_id for s in signals}


def test_empty_input_gives_no_signals():
    assert detect_all(RULES, [], []) == []


def test_naive_timestamps_are_treated_as_utc():
    facts = run([5, 5, 5])
    naive = [replace(c, occurred_at=c.occurred_at.replace(tzinfo=None)) for c in facts]
    assert detect_impossible_timing(RULES, naive) == detect_impossible_timing(RULES, facts)


def test_signal_is_frozen_and_never_confirmed():
    [signal] = detect_replay(RULES, replays(5, 1))
    with pytest.raises(AttributeError):
        signal.status = "confirmed"  # type: ignore[misc]
    assert isinstance(signal, Signal) and signal.status == "suspected"
    assert signal.detected_window[0] <= signal.detected_window[1]
