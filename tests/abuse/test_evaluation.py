"""M6 efficacy on seeded synthetic personas (real ingestion, injected clock, fixed seeds)."""

import pytest

from src.abuse.detectors import Signal
from src.abuse.evaluation import ATTACKER, BENIGN, BORDERLINE, LabeledLearner, evaluate
from tests.abuse.fixtures import PERSONAS, SEED
from tests.abuse.run_evaluation import run_once

ATTACKER_TYPES = {
    "fast_completer": {"impossible_timing"},
    "metronomic_bot": {"scripted_farming"},
    "lesson_farmer": {"scripted_farming"},
    "replay_attacker": {"replay"},
    "duplicate_exploiter": {"duplicate_exploit"},
    "reward_field_injector": {"duplicate_exploit"},
    "review_label_spoofer": {"scripted_farming"},
    "orphan_reviewer": {"scripted_farming"},
    "timestamp_spoofer": {"impossible_timing"},
}


def sig(learner, signal_type, refs=("event:e",)):
    return Signal(f"s-{learner}-{signal_type}", learner, signal_type, "suspected", "r", "v1", "d" * 64,
                  tuple(refs), ("a", "b"))


# -- the metric arithmetic itself ----------------------------------------------------


def test_precision_recall_and_false_positive_arithmetic():
    labels = [
        LabeledLearner("a1", "att1", ATTACKER, frozenset({"replay"})),
        LabeledLearner("a2", "att2", ATTACKER, frozenset({"replay"})),
        LabeledLearner("b1", "ben1", BENIGN, frozenset()),
        LabeledLearner("b2", "ben2", BENIGN, frozenset()),
    ]
    signals = [sig("a1", "replay"), sig("b1", "replay"), sig("a2", "scripted_farming")]
    report = evaluate(labels, signals)
    replay = report.per_type["replay"]
    assert (replay.true_positives, replay.false_positives, replay.false_negatives) == (1, 1, 1)
    assert replay.precision == 0.5 and replay.recall == 0.5
    farming = report.per_type["scripted_farming"]
    assert (farming.true_positives, farming.false_positives) == (0, 1)
    assert farming.precision == 0.0 and farming.recall is None
    assert report.per_type["impossible_timing"].precision is None
    assert (report.benign_learners, report.benign_false_positives) == (2, 1)
    assert (report.attacker_learners, report.attackers_fully_detected) == (2, 1)


def test_a_dead_detector_would_fail_the_efficacy_checks():
    labels = [LabeledLearner("a1", "att1", ATTACKER, frozenset({"replay"}))]
    report = evaluate(labels, [])
    assert report.per_type["replay"].recall == 0.0
    assert report.attackers_fully_detected == 0


def test_borderline_learners_are_reported_not_scored():
    labels = [
        LabeledLearner("x", "miss", BORDERLINE, frozenset({"replay"}), frozenset()),
        LabeledLearner("y", "fp", BORDERLINE, frozenset(), frozenset({"scripted_farming"})),
        LabeledLearner("z", "surprise", BORDERLINE, frozenset(), frozenset()),
    ]
    report = evaluate(labels, [sig("y", "scripted_farming"), sig("z", "replay")])
    assert [(b.persona, b.behaved_as_labeled) for b in report.borderline] == [
        ("fp", True), ("miss", True), ("surprise", False)]
    assert all(m.false_positives == m.true_positives == m.false_negatives == 0 for m in report.per_type.values())


def test_evidence_backed_rate_counts_unresolvable_refs():
    labels = [LabeledLearner("a", "p", ATTACKER, frozenset({"replay"}))]
    signals = [sig("a", "replay", ("event:good",)), sig("a", "scripted_farming", ("event:good", "event:ghost"))]
    report = evaluate(labels, signals, lambda s, ref: ref != "event:ghost")
    assert report.evidence_backed_rate == 0.5
    assert evaluate(labels, []).evidence_backed_rate is None


# -- the personas ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    return run_once(str(tmp_path_factory.mktemp("eval")))


def test_every_persona_is_present_and_labeled(result):
    labels, _, _, _ = result
    assert len(PERSONAS) == len(labels) == 16
    assert {l.persona for l in labels if l.category == ATTACKER} == set(ATTACKER_TYPES)
    assert {l.persona for l in labels if l.category == BENIGN} == {
        "steady_learner", "bursty_but_human", "spaced_repetition_reviewer", "flaky_network_learner",
        "honest_offline_sync"}
    assert {l.persona for l in labels if l.category == BORDERLINE} == {
        "slow_bot_with_jitter", "perfectly_regular_human"}
    for label in labels:
        if label.category == ATTACKER:
            assert label.truth_types == ATTACKER_TYPES[label.persona]


def test_no_false_positives_on_benign_personas(result):
    _, summary, report, _ = result
    assert report.benign_learners == 5 and report.benign_false_positives == 0
    labels = result[0]
    benign_ids = {l.learner_id for l in labels if l.category == BENIGN}
    assert [s for s in summary.signals if s.learner_id in benign_ids] == []


def test_every_designed_attacker_is_detected_with_its_type(result):
    labels, summary, report, _ = result
    assert report.attacker_learners == 9 and report.attackers_fully_detected == 9
    for signal_type, metrics in report.per_type.items():
        assert metrics.recall == 1.0, signal_type
        assert metrics.precision == 1.0, signal_type
    by_persona = {}
    names = {l.learner_id: l.persona for l in labels}
    for signal in summary.signals:
        by_persona.setdefault(names[signal.learner_id], set()).add(signal.signal_type)
    for persona, expected in ATTACKER_TYPES.items():
        assert by_persona[persona] == expected


def test_evidence_backed_rate_is_one_hundred_percent(result):
    _, summary, report, _ = result
    assert summary.signals and report.evidence_backed_rate == 1.0


def test_borderline_personas_behave_as_labeled(result):
    _, _, report, _ = result
    got = {b.persona: b for b in report.borderline}
    assert got["slow_bot_with_jitter"].detected_types == ()  # expected miss
    assert got["slow_bot_with_jitter"].truth_types == ("scripted_farming",)
    assert got["perfectly_regular_human"].detected_types == ("scripted_farming",)  # expected false positive
    assert got["perfectly_regular_human"].truth_types == ()
    assert all(b.behaved_as_labeled for b in report.borderline)


def test_results_are_identical_across_two_runs(tmp_path):
    def signature(directory):
        labels, summary, report, _ = run_once(str(directory))
        names = {l.learner_id: l.persona for l in labels}
        return sorted(
            (names[s.learner_id], s.signal_type, s.rule_id, s.evidence_refs, s.detected_window)
            for s in summary.signals
        ), report

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first, report_a = signature(tmp_path / "a")
    second, report_b = signature(tmp_path / "b")
    assert first == second
    assert report_a == report_b


def test_a_different_seed_still_separates_the_labeled_personas(tmp_path):
    _, _, report, _ = run_once(str(tmp_path), seed=SEED + 1)
    assert report.benign_false_positives == 0
    assert report.attackers_fully_detected == report.attacker_learners


def test_latency_has_a_generous_sanity_bound(tmp_path):
    _, summary, _, elapsed = run_once(str(tmp_path), copies=4)
    assert summary.events_read > 1500
    assert elapsed < 60  # reported in the docs, not a performance requirement


def _rules_by_persona(result):
    labels, summary, _, _ = result
    names = {l.learner_id: l.persona for l in labels}
    found = {}
    for signal in summary.signals:
        found.setdefault(names[signal.learner_id], set()).add(signal.rule_id)
    return found


def test_gap_fix_personas_are_caught_by_their_own_rules(result):
    found = _rules_by_persona(result)
    assert found["review_label_spoofer"] == {"scripted_farming.review_repeat"}
    assert found["orphan_reviewer"] == {"scripted_farming.orphan_review"}
    assert found["timestamp_spoofer"] == {"impossible_timing.receive_burst"}


def test_honest_offline_sync_is_not_flagged(result):
    assert "honest_offline_sync" not in _rules_by_persona(result)


def test_the_gap_fix_attackers_evade_the_original_v1_rules(tmp_path):
    from src.abuse.service import AbuseDetectionService
    from src.abuse.store import AbuseSignalStore
    from tests.abuse.fixtures import build_population
    from tests.abuse.harness import Harness

    h = Harness(str(tmp_path / "events.db"))
    try:
        labels = build_population(h)
        summary = AbuseDetectionService(h.application, h.evidence, AbuseSignalStore(h.db)).run(
            shadow=True, rules_version="v1")
        evaded = {"review_label_spoofer", "orphan_reviewer", "timestamp_spoofer"}
        names = {l.learner_id: l.persona for l in labels}
        assert evaded.isdisjoint({names[s.learner_id] for s in summary.signals})
    finally:
        h.close()
