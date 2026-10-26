"""M6 gap fix: detect misuse of the review/remediation exemption (flag-only)."""

import json
import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from importlib.resources import files

import pytest
from jsonschema import Draft7Validator

from src.abuse.detectors import CompletionFact, detect_all, detect_review_misuse
from src.abuse.rules import get_abuse_rules
from src.abuse.service import AbuseDetectionService
from src.abuse.store import AbuseSignalStore
from src.state_machine.rules import _REGISTRY, load_decay_rulesets
from tests.abuse.fixtures import SEED, spaced_repetition_reviewer
from tests.abuse.harness import EPOCH, Harness

V1, V2 = get_abuse_rules("v1"), get_abuse_rules("v2")
T0 = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
SCHEMA = Draft7Validator(
    json.loads((files("src") / "schemas" / "events" / "abuse_signal_raised.schema.json").read_text())
)
ORPHAN, REPEAT = "scripted_farming.orphan_review", "scripted_farming.review_repeat"


def comp(i, seconds, lesson, kind, learner="L1"):
    return CompletionFact(learner, f"c{i:03d}", f"e{i:03d}", lesson, kind,
                          T0 + timedelta(seconds=seconds), 0, 100)


def rule_ids(signals):
    return sorted(s.rule_id for s in signals)


def check(signals, facts):
    refs = {f"completion:{c.completion_id}" for c in facts} | {f"event:{c.event_id}" for c in facts}
    for s in signals:
        assert s.signal_type == "scripted_farming" and s.status == "suspected"
        assert s.evidence_refs and set(s.evidence_refs) <= refs
        assert all(r.startswith(("completion:", "event:")) for r in s.evidence_refs)
        SCHEMA.validate(s.to_payload())
        assert s.rule_version == "v2" and s.rule_digest == V2.digest
    return signals


# -- orphan_review -------------------------------------------------------------------------


def orphans(n, kind="review"):
    return [comp(i, i * 600, f"l{i}", kind) for i in range(n)]


@pytest.mark.parametrize("kind", ["review", "remediation"])
def test_orphan_boundary_for_both_exempt_kinds(kind):
    assert detect_review_misuse(V2, orphans(2, kind)) == []
    facts = orphans(3, kind)
    [signal] = check(detect_review_misuse(V2, facts), facts)
    assert signal.rule_id == ORPHAN
    assert len(signal.evidence_refs) == 6  # 3 completions x (completion, event)
    assert rule_ids(detect_review_misuse(V2, orphans(4, kind))) == [ORPHAN]


def test_review_after_a_normal_completion_is_not_orphan():
    facts = [f for i in range(3) for f in (comp(2 * i, i * 1000, f"l{i}", "lesson"),
                                           comp(2 * i + 1, i * 1000 + 500, f"l{i}", "review"))]
    assert detect_review_misuse(V2, facts) == []


def test_normal_completion_after_the_review_does_not_rescue_it():
    facts = [f for i in range(3) for f in (comp(2 * i, i * 1000, f"l{i}", "review"),
                                           comp(2 * i + 1, i * 1000 + 500, f"l{i}", "lesson"))]
    assert rule_ids(detect_review_misuse(V2, facts)) == [ORPHAN]


def test_kind_none_counts_as_a_lesson():
    facts = [f for i in range(3) for f in (comp(2 * i, i * 1000, f"l{i}", None),
                                           comp(2 * i + 1, i * 1000 + 500, f"l{i}", "review"))]
    assert detect_review_misuse(V2, facts) == []


def test_only_distinct_lessons_count_and_only_two_orphans_of_five_do_not_flag():
    same_lesson = [comp(i, i * 600, "l0", "review") for i in range(6)]
    assert [s.rule_id for s in detect_review_misuse(V2, same_lesson)] == [REPEAT]  # not orphan: 1 lesson
    mixed = orphans(2) + [comp(10 + i, 9000 + i * 10, f"n{i}", "lesson") for i in range(3)]
    assert detect_review_misuse(V2, mixed) == []


def test_identical_timestamps_order_by_completion_id():
    # lesson completion id sorts before the review id at the same instant -> review is rescued
    saved = [f for i in range(3) for f in (comp(2 * i, 0, f"l{i}", "lesson"), comp(2 * i + 1, 0, f"l{i}", "review"))]
    assert detect_review_misuse(V2, saved) == []
    # review id sorts first -> not rescued
    lost = [f for i in range(3) for f in (comp(2 * i, 0, f"l{i}", "review"), comp(2 * i + 1, 0, f"l{i}", "lesson"))]
    assert rule_ids(detect_review_misuse(V2, lost)) == [ORPHAN]


# -- review_repeat ---------------------------------------------------------------------------


def reviews(n, spacing, lesson="l0", kind="review", start=0, first_id=0):
    return [comp(first_id + i, start + i * spacing, lesson, kind) for i in range(n)]


def test_review_repeat_boundary():
    assert detect_review_misuse(V2, reviews(3, 600)) == []
    facts = reviews(4, 600)
    [signal] = check(detect_review_misuse(V2, facts), facts)
    assert signal.rule_id == REPEAT and len(signal.evidence_refs) == 8
    assert rule_ids(detect_review_misuse(V2, reviews(9, 600))) == [REPEAT]


def test_review_repeat_window_is_inclusive():
    assert rule_ids(detect_review_misuse(V2, reviews(4, 8 * 3600))) == [REPEAT]  # span exactly 24 h
    assert detect_review_misuse(V2, reviews(4, 8 * 3600 + 1)) == []


def test_review_repeat_lessons_are_independent_and_normal_completions_do_not_count():
    split = reviews(2, 600, "a") + reviews(2, 600, "b", first_id=10)
    assert detect_review_misuse(V2, split) == []
    with_lessons = reviews(3, 600) + [comp(50 + i, 100 + i, "l0", "lesson") for i in range(5)]
    assert detect_review_misuse(V2, with_lessons) == []
    two = reviews(4, 600, "a") + reviews(4, 600, "b", first_id=10)
    assert [s.rule_id for s in detect_review_misuse(V2, two)].count(REPEAT) == 2


def test_review_repeat_counts_remediation_and_review_together():
    facts = [comp(0, 0, "l0", "review"), comp(1, 10, "l0", "remediation"),
             comp(2, 20, "l0", "review"), comp(3, 30, "l0", "remediation")]
    assert rule_ids(detect_review_misuse(V2, facts)) == [REPEAT]


# -- cross-cutting -----------------------------------------------------------------------------


def corpus():
    return reviews(6, 90) + orphans(3) + [replace(c, learner_id="L2", completion_id="x" + c.completion_id,
                                                  event_id="x" + c.event_id) for c in reviews(5, 60)]


def test_v1_rules_produce_nothing():
    assert V1.review_misuse is None
    assert detect_review_misuse(V1, corpus()) == []
    assert not [s for s in detect_all(V1, corpus(), []) if s.rule_id in (ORPHAN, REPEAT)]


def test_empty_input():
    assert detect_review_misuse(V2, []) == []


def test_naive_timestamps_are_utc():
    facts = reviews(4, 600)
    naive = [replace(c, occurred_at=c.occurred_at.replace(tzinfo=None)) for c in facts]
    assert detect_review_misuse(V2, naive) == detect_review_misuse(V2, facts) != []


def test_shuffled_input_gives_identical_signals_and_detect_all_includes_them():
    facts = corpus()
    first = detect_review_misuse(V2, facts)
    shuffled = facts[:]
    random.Random(4).shuffle(shuffled)
    assert detect_review_misuse(V2, shuffled) == first
    assert first == sorted(first, key=lambda s: (s.learner_id, s.signal_type, s.rule_id, s.signal_id))
    assert {s.rule_id for s in first} == {ORPHAN, REPEAT}
    assert set(first) <= set(detect_all(V2, facts, []))
    check(first, facts)


def test_existing_farming_rules_are_unchanged_for_exempt_kinds():
    from src.abuse.detectors import detect_scripted_farming

    assert detect_scripted_farming(V2, reviews(20, 700)) == []


# -- end to end ----------------------------------------------------------------------------------


@pytest.fixture
def v2_harness(tmp_path, monkeypatch):
    for version, ruleset in load_decay_rulesets().items():
        monkeypatch.setitem(_REGISTRY, version, ruleset)
    h = Harness(str(tmp_path / "events.db"), rule_version="v2")
    yield h
    h.close()


def test_fifteen_reviews_of_one_lesson_are_flagged_and_detection_is_flag_only_leaving_xp_unchanged(v2_harness):
    h, rng = v2_harness, random.Random(SEED)
    learner, t = h.new_learner(), EPOCH
    for _ in range(15):
        assert h.complete(learner, t, lesson_id="farmed", kind="review").status == "accepted"
        t += timedelta(seconds=rng.uniform(40, 200))
    xp_before, version_before = h.learner(learner).xp_total, h.learner(learner).version
    events_before = len(h.notifications())
    assert xp_before == 1500  # the exemption really does pay full price
    store = AbuseSignalStore(h.db)
    summary = AbuseDetectionService(h.application, h.evidence, store).run()
    assert [s.rule_id for s in summary.signals] == [REPEAT]
    assert {s.status for s in summary.signals} == {"suspected"}
    assert store.count() == 1
    after = h.learner(learner)
    assert (after.xp_total, after.version) == (xp_before, version_before)
    assert len(h.notifications()) == events_before


def test_the_benign_spaced_repetition_persona_produces_no_review_misuse_signals(tmp_path):
    h = Harness(str(tmp_path / "events.db"))
    try:
        label = spaced_repetition_reviewer(h, random.Random(SEED), EPOCH)
        summary = AbuseDetectionService(h.application, h.evidence, AbuseSignalStore(h.db)).run()
        assert [s for s in summary.signals if s.learner_id == label.learner_id] == []
    finally:
        h.close()
