"""M4 step 2: pure remediation policy, written before the implementation.

Assumed new API (see the Week 6 M4 step 2 audit): ``src.interventions.config.RemediationConfig``,
``src.interventions.policy.RemediationDecision`` and ``decide_remediation``. Inputs are plain
dicts shaped like ``Learner.checkpoint_attempts`` / ``Learner.first_checkpoint_attempt_ids``.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.interventions.config import RemediationConfig
from src.interventions.policy import RemediationDecision, decide_remediation

NOW = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)
CONFIG = RemediationConfig()


def attempt(attempt_id, assessment_id="a1", passed=False, occurred_at=NOW):
    return {
        "attempt_id": attempt_id,
        "assessment_id": assessment_id,
        "assessment_version": 1,
        "passed": passed,
        "timezone": "UTC",
        "occurred_at": occurred_at,
    }


def prior(attempt_id, action, decided_at):
    return RemediationDecision(
        decision_id=f"remediation:{attempt_id}",
        attempt_id=attempt_id,
        assessment_id="a0",
        action=action,
        reason=None if action == "assign" else "passed",
        decided_at=decided_at,
        deadline_at=decided_at + timedelta(days=7) if action == "assign" else None,
        quest_id=CONFIG.quest_id if action == "assign" else None,
        policy_version=CONFIG.policy_version,
    )


def decide(attempts, first_ids, priors=(), attempt_id="att-1", now=NOW):
    return decide_remediation(attempts, first_ids, priors, attempt_id, now, CONFIG)


def test_config_defaults():
    assert CONFIG.frequency_window == timedelta(days=14)
    assert CONFIG.quest_deadline == timedelta(days=7)
    assert CONFIG.quest_id == "remediation_v1"
    assert CONFIG.policy_version == "remediation_policy_v1"


def test_config_is_frozen():
    with pytest.raises(Exception):
        CONFIG.quest_id = "other"


def test_failed_first_attempt_assigns():
    d = decide({"att-1": attempt("att-1")}, {"a1": "att-1"})
    assert d.action == "assign"
    assert d.reason is None
    assert d.decision_id == "remediation:att-1"
    assert d.attempt_id == "att-1"
    assert d.assessment_id == "a1"
    assert d.decided_at == NOW
    assert d.deadline_at == NOW + timedelta(days=7)
    assert d.quest_id == "remediation_v1"
    assert d.policy_version == "remediation_policy_v1"


def test_decision_is_frozen():
    d = decide({"att-1": attempt("att-1")}, {"a1": "att-1"})
    with pytest.raises(Exception):
        d.action = "no_action"


def test_passed_first_attempt_is_no_action():
    d = decide({"att-1": attempt("att-1", passed=True)}, {"a1": "att-1"})
    assert d.action == "no_action"
    assert d.reason == "passed"
    assert d.deadline_at is None
    assert d.quest_id is None
    assert d.decision_id == "remediation:att-1"


def test_failed_second_attempt_is_not_first():
    attempts = {
        "att-0": attempt("att-0", occurred_at=NOW - timedelta(days=1)),
        "att-1": attempt("att-1"),
    }
    d = decide(attempts, {"a1": "att-0"})
    assert d.action == "no_action"
    assert d.reason == "not_first_attempt"
    assert d.deadline_at is None
    assert d.quest_id is None


def test_not_first_checked_before_passed():
    attempts = {
        "att-0": attempt("att-0", occurred_at=NOW - timedelta(days=1)),
        "att-1": attempt("att-1", passed=True),
    }
    d = decide(attempts, {"a1": "att-0"})
    assert d.reason == "not_first_attempt"


def test_prior_assign_three_days_ago_blocks():
    priors = [prior("old", "assign", NOW - timedelta(days=3))]
    d = decide({"att-1": attempt("att-1")}, {"a1": "att-1"}, priors)
    assert d.action == "no_action"
    assert d.reason == "remediated_within_window"
    assert d.deadline_at is None
    assert d.quest_id is None


def test_passed_checked_before_window():
    priors = [prior("old", "assign", NOW - timedelta(days=3))]
    d = decide({"att-1": attempt("att-1", passed=True)}, {"a1": "att-1"}, priors)
    assert d.reason == "passed"


def test_prior_assign_exactly_14_days_ago_does_not_block():
    priors = [prior("old", "assign", NOW - timedelta(days=14))]
    d = decide({"att-1": attempt("att-1")}, {"a1": "att-1"}, priors)
    assert d.action == "assign"


def test_prior_assign_just_inside_window_blocks():
    priors = [prior("old", "assign", NOW - timedelta(days=14) + timedelta(seconds=1))]
    d = decide({"att-1": attempt("att-1")}, {"a1": "att-1"}, priors)
    assert d.action == "no_action"
    assert d.reason == "remediated_within_window"


def test_prior_no_action_does_not_block():
    priors = [prior("old", "no_action", NOW - timedelta(days=1))]
    d = decide({"att-1": attempt("att-1")}, {"a1": "att-1"}, priors)
    assert d.action == "assign"


def test_any_blocking_prior_among_several_blocks():
    priors = [
        prior("p1", "assign", NOW - timedelta(days=30)),
        prior("p2", "no_action", NOW - timedelta(days=1)),
        prior("p3", "assign", NOW - timedelta(days=5)),
    ]
    d = decide({"att-1": attempt("att-1")}, {"a1": "att-1"}, iter(priors))
    assert d.reason == "remediated_within_window"


def test_unknown_attempt_id_raises_key_error():
    with pytest.raises(KeyError):
        decide({"att-1": attempt("att-1")}, {"a1": "att-1"}, attempt_id="missing")


def test_unknown_attempt_checked_before_naive_now():
    with pytest.raises(KeyError):
        decide({}, {}, attempt_id="missing", now=datetime(2026, 3, 20, 12, 0))


def test_naive_now_raises_value_error():
    with pytest.raises(ValueError):
        decide({"att-1": attempt("att-1")}, {"a1": "att-1"}, now=datetime(2026, 3, 20, 12, 0))


def test_deterministic():
    attempts = {"att-1": attempt("att-1")}
    priors = [prior("old", "no_action", NOW - timedelta(days=1))]
    first = decide(attempts, {"a1": "att-1"}, priors)
    second = decide(attempts, {"a1": "att-1"}, priors)
    assert first == second


def test_inputs_not_mutated():
    attempts = {"att-1": attempt("att-1")}
    first_ids = {"a1": "att-1"}
    snapshot = ({k: dict(v) for k, v in attempts.items()}, dict(first_ids))
    decide(attempts, first_ids)
    assert ({k: dict(v) for k, v in attempts.items()}, dict(first_ids)) == snapshot
