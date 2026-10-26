"""M6 gap fix: timing rules on the trusted receive time, not only the producer-claimed one."""

import json
import random
from dataclasses import fields, replace
from datetime import datetime, timedelta, timezone
from importlib.resources import files

import pytest
from jsonschema import Draft7Validator

from src.abuse.detectors import CompletionFact, detect_all, detect_receive_timing
from src.abuse.rules import get_abuse_rules
from src.abuse.service import AbuseDetectionService
from src.abuse.store import AbuseSignalStore
from tests.abuse.harness import EPOCH, Harness

V1, V2 = get_abuse_rules("v1"), get_abuse_rules("v2")
T0 = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)
SCHEMA = Draft7Validator(
    json.loads((files("src") / "schemas" / "events" / "abuse_signal_raised.schema.json").read_text())
)
BURST, FUTURE = "impossible_timing.receive_burst", "impossible_timing.future_dated"


def comp(i, received_s, *, claimed_s=None, learner="L1", received=True):
    """Claimed time defaults to just before the receive time (an honest client)."""
    claimed = received_s - 1 if claimed_s is None else claimed_s
    return CompletionFact(
        learner, f"c{i:03d}", f"e{i:03d}", f"lesson-{i}", "lesson", T0 + timedelta(seconds=claimed), 0, 100,
        T0 + timedelta(seconds=received_s) if received else None,
    )


def burst(n, spacing, **kw):
    return [comp(i, i * spacing, **kw) for i in range(n)]


def ids(signals):
    return sorted(s.rule_id for s in signals)


def check(signals, facts):
    refs = {f"completion:{c.completion_id}" for c in facts} | {f"event:{c.event_id}" for c in facts}
    for s in signals:
        assert s.signal_type == "impossible_timing" and s.status == "suspected"
        assert s.evidence_refs and set(s.evidence_refs) <= refs
        SCHEMA.validate(s.to_payload())
        assert s.rule_version == "v2" and s.rule_digest == V2.digest
    return signals


def test_received_at_is_the_last_optional_field_and_old_constructions_still_work():
    assert [f.name for f in fields(CompletionFact)][-1] == "received_at"
    fact = CompletionFact("L", "c", "e", "l", "lesson", T0, 0, 100)
    assert fact.received_at is None


# -- receive_burst ------------------------------------------------------------------------


def test_receive_burst_boundary():
    assert detect_receive_timing(V2, burst(40, 10)) == []
    facts = burst(41, 10)
    [signal] = check(detect_receive_timing(V2, facts), facts)
    assert signal.rule_id == BURST and len(signal.evidence_refs) == 82
    assert ids(detect_receive_timing(V2, burst(60, 5))) == [BURST]


def test_receive_burst_window_is_inclusive():
    assert ids(detect_receive_timing(V2, burst(41, 15))) == [BURST]  # span exactly 600 s
    assert detect_receive_timing(V2, burst(41, 15.001)) == []


def test_completions_without_received_at_are_ignored():
    mixed = burst(60, 5, received=False)
    assert detect_receive_timing(V2, mixed) == []
    half = burst(41, 5)
    half[10] = replace(half[10], received_at=None)  # leaves 40 with a receive time
    assert detect_receive_timing(V2, half) == []


def test_learners_are_independent():
    two = burst(25, 5) + [replace(c, learner_id="L2", completion_id="x" + c.completion_id,
                                  event_id="x" + c.event_id) for c in burst(25, 5)]
    assert detect_receive_timing(V2, two) == []


def test_honest_offline_sync_is_not_flagged():
    facts = [comp(i, i * 6, claimed_s=-3600 + i * 300) for i in range(10)]  # claimed 5 min apart, received in 1 min
    assert detect_receive_timing(V2, facts) == []


# -- future_dated -------------------------------------------------------------------------------


def future(n, ahead_s):
    return [comp(i, i * 100, claimed_s=i * 100 + ahead_s) for i in range(n)]


def test_future_dated_boundary():
    assert detect_receive_timing(V2, future(2, 360)) == []
    facts = future(3, 360)
    [signal] = check(detect_receive_timing(V2, facts), facts)
    assert signal.rule_id == FUTURE and len(signal.evidence_refs) == 6
    assert ids(detect_receive_timing(V2, future(5, 360))) == [FUTURE]


def test_exactly_the_tolerance_does_not_count():
    assert detect_receive_timing(V2, future(3, 300)) == []
    assert ids(detect_receive_timing(V2, future(3, 300.001))) == [FUTURE]
    mixed = future(2, 360) + [comp(9, 900, claimed_s=1200)]  # third is exactly +5 min
    assert detect_receive_timing(V2, mixed) == []


def test_future_dated_ignores_missing_received_at_and_other_learners():
    assert detect_receive_timing(V2, [replace(c, received_at=None) for c in future(5, 600)]) == []
    split = future(2, 600) + [replace(c, learner_id="L2", completion_id="y" + c.completion_id) for c in future(2, 600)]
    assert detect_receive_timing(V2, split) == []


# -- cross-cutting ----------------------------------------------------------------------------------


def corpus():
    return burst(45, 5) + [replace(c, learner_id="L3", completion_id="f" + c.completion_id,
                                                      event_id="f" + c.event_id) for c in future(4, 900)]


def test_v1_rules_produce_nothing_and_empty_input_is_fine():
    assert (V1.receive_burst, V1.future_dated) == (None, None)
    assert detect_receive_timing(V1, corpus()) == []
    assert detect_receive_timing(V2, []) == []


def test_naive_timestamps_are_utc():
    facts = burst(41, 10)
    naive = [replace(c, occurred_at=c.occurred_at.replace(tzinfo=None),
                     received_at=c.received_at.replace(tzinfo=None)) for c in facts]
    assert detect_receive_timing(V2, naive) == detect_receive_timing(V2, facts) != []


def test_output_is_order_independent_and_part_of_detect_all():
    facts = corpus()
    first = detect_receive_timing(V2, facts)
    assert {s.rule_id for s in first} == {BURST, FUTURE}
    shuffled = facts[:]
    random.Random(2).shuffle(shuffled)
    assert detect_receive_timing(V2, shuffled) == first
    assert set(first) <= set(detect_all(V2, facts, []))
    check(first, facts)


# -- service: received_at comes from the delivery binding ------------------------------------------------


def test_service_fills_received_at_from_the_delivery_binding(tmp_path):
    h = Harness(str(tmp_path / "e.db"))
    try:
        learner = h.new_learner()
        received = EPOCH + timedelta(hours=30)
        h.complete(learner, EPOCH, received_at=received)
        service = AbuseDetectionService(h.application, h.evidence, AbuseSignalStore(h.db))
        [fact] = service.completion_facts()[0]
        assert fact.received_at == received and fact.received_at.tzinfo is not None

        class NoBindings:
            def delivery_binding(self, event_id):
                return None

        stub = AbuseDetectionService(h.application, NoBindings(), AbuseSignalStore(h.db))
        assert stub.completion_facts()[0][0].received_at is None
    finally:
        h.close()


def test_binding_without_received_at_gives_none(tmp_path):
    from src.ingestion.evidence import DeliveryBinding

    h = Harness(str(tmp_path / "e.db"))
    try:
        h.complete(h.new_learner(), EPOCH)

        class OldRows:
            def delivery_binding(self, event_id):
                return DeliveryBinding(event_id, "l", "a", None)

        service = AbuseDetectionService(h.application, OldRows(), AbuseSignalStore(h.db))
        assert service.completion_facts()[0][0].received_at is None
    finally:
        h.close()


# -- end to end: the original gap ----------------------------------------------------------------------------


def test_spoofed_spacing_with_a_fast_delivery_burst_is_flagged_only_by_receive_burst(tmp_path):
    h, rng = Harness(str(tmp_path / "e.db")), random.Random(3)
    try:
        learner, claimed, claims = h.new_learner(), EPOCH, []
        for _ in range(60):
            claims.append(claimed)
            claimed += timedelta(minutes=rng.uniform(3, 9))
        delivery = EPOCH + timedelta(hours=30)
        for i, when in enumerate(claims):
            result = h.complete(learner, when, lesson_id=f"spoof-{i}", received_at=delivery + timedelta(seconds=2 * i))
            assert result.status == "accepted"
        assert h.learner(learner).xp_total == 6000
        store = AbuseSignalStore(h.db)
        service = AbuseDetectionService(h.application, h.evidence, store)

        old_only = get_abuse_rules("v1")
        assert service.run(shadow=True, rules_version=old_only).signals == []  # the original gap
        summary = service.run()
        assert [s.rule_id for s in summary.signals] == [BURST]
        assert {s.status for s in summary.signals} == {"suspected"}
        assert h.learner(learner).xp_total == 6000
        # the claimed-time rules of the same rule set stay silent too
        rest = [s for s in summary.signals if s.rule_id != BURST]
        assert rest == []
    finally:
        h.close()
