"""M4 step 3: the remediation quest definition and completion-kind filtering.

Written before the implementation. Assumed new API (see the Week 6 M4 step 3 audit):
``src.state_machine.quest_definitions.quest_definition_for(quest_id, definition_version)``
returning a frozen ``QuestDefinition(quest_id, definition_version, target,
accepted_completion_kinds)``, and ``Learner.complete_lesson`` progressing a quest only for
completion kinds the quest's definition accepts (``None`` = any kind).
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.state_machine.learner import Learner

ASSIGNED = datetime(2026, 9, 1, tzinfo=timezone.utc)
OCCURRED = datetime(2026, 9, 2, 9, 0, tzinfo=timezone.utc)


def _definition(quest_id, definition_version):
    # Imported here so a missing feature fails each test with a clear ImportError.
    from src.state_machine.quest_definitions import quest_definition_for

    return quest_definition_for(quest_id, definition_version)


def _remediation_learner() -> Learner:
    learner = Learner()
    assert learner.assign_quest(
        "remediation_v1",
        assignment_id="remediation:att-1",
        assigned_at=ASSIGNED,
        deadline_at=ASSIGNED + timedelta(days=7),
        definition_version="remediation_quests_v1",
    )
    learner.collect_events()
    return learner


def _complete(learner: Learner, completion_id: str, kind: str | None, hours: int = 0) -> list[str]:
    learner.complete_lesson(
        completion_id,
        "lesson-1",
        occurred_at=OCCURRED + timedelta(hours=hours),
        timezone="UTC",
        completion_kind=kind,
    )
    return [type(e).__name__ for e in learner.collect_events()]


# --- definition lookup --------------------------------------------------------


def test_rule_set_quest_definition_comes_from_the_rule_set():
    definition = _definition("lesson_starter_v1", "v1")
    assert definition.quest_id == "lesson_starter_v1"
    assert definition.definition_version == "v1"
    assert definition.target == 3
    assert definition.accepted_completion_kinds is None


def test_remediation_quest_definition():
    definition = _definition("remediation_v1", "remediation_quests_v1")
    assert definition.quest_id == "remediation_v1"
    assert definition.definition_version == "remediation_quests_v1"
    assert definition.target == 1
    assert definition.accepted_completion_kinds == frozenset({"remediation"})


def test_quest_definition_is_frozen():
    definition = _definition("remediation_v1", "remediation_quests_v1")
    with pytest.raises(Exception):
        definition.target = 5


def test_unknown_quest_id_raises_key_error():
    with pytest.raises(KeyError):
        _definition("no_such_quest", "remediation_quests_v1")


def test_unknown_definition_version_raises_key_error():
    with pytest.raises(KeyError):
        _definition("remediation_v1", "no_such_version")


# --- assigning and progressing the remediation quest -------------------------


def test_assign_remediation_quest_succeeds():
    learner = Learner()
    assert learner.assign_quest(
        "remediation_v1",
        assignment_id="remediation:att-1",
        assigned_at=ASSIGNED,
        definition_version="remediation_quests_v1",
    ) is True
    instance = learner.quest_instances["remediation:att-1"]
    assert instance["quest_id"] == "remediation_v1"
    assert instance["definition_version"] == "remediation_quests_v1"
    assert instance["status"] == "assigned"


@pytest.mark.parametrize("kind", ("lesson", None, "review"))
def test_non_remediation_completion_does_not_progress_the_quest(kind):
    learner = _remediation_learner()

    names = _complete(learner, "c-1", kind)

    assert "QuestProgressed" not in names
    assert "QuestCompleted" not in names
    instance = learner.quest_instances["remediation:att-1"]
    assert instance["status"] == "assigned"
    assert instance["progress"] == 0
    assert learner.quest_progress["remediation:att-1"] == 0


def test_non_remediation_completions_still_earn_xp():
    # The kind filter applies to quest progress only; the completion itself is unchanged.
    learner = _remediation_learner()
    _complete(learner, "c-1", "lesson")
    assert learner.xp_total > 0


def test_one_remediation_completion_completes_the_quest():
    learner = _remediation_learner()

    names = _complete(learner, "c-1", "remediation")

    assert "QuestCompleted" in names
    instance = learner.quest_instances["remediation:att-1"]
    assert instance["status"] == "completed"
    assert instance["progress"] == 1
    assert "quest_completion:remediation:att-1" in learner.completed_quest_ids


def test_remediation_completion_after_ignored_lessons_still_completes():
    learner = _remediation_learner()
    for index, kind in enumerate(("lesson", None, "review")):
        _complete(learner, f"c-{index}", kind, hours=index)

    names = _complete(learner, "c-remediation", "remediation", hours=5)

    assert "QuestCompleted" in names
    assert learner.quest_instances["remediation:att-1"]["status"] == "completed"


# --- regression: existing quest unchanged -----------------------------------


@pytest.mark.parametrize("kind", (None, "lesson", "review", "remediation"))
def test_lesson_starter_still_progresses_on_every_completion_kind(kind):
    learner = Learner()
    learner.assign_quest(
        "lesson_starter_v1",
        assignment_id="starter-1",
        assigned_at=ASSIGNED,
    )
    learner.collect_events()

    names = []
    for index in range(3):
        names += _complete(learner, f"c-{index}", kind, hours=index)

    assert names.count("QuestProgressed") == 3
    assert "QuestCompleted" in names
    assert learner.quest_instances["starter-1"]["status"] == "completed"


# --- edge cases ---------------------------------------------------------------


def test_remediation_completion_before_assigned_at_does_not_progress():
    learner = _remediation_learner()

    learner.complete_lesson(
        "c-early",
        "lesson-1",
        occurred_at=ASSIGNED - timedelta(hours=1),
        timezone="UTC",
        completion_kind="remediation",
    )
    names = [type(e).__name__ for e in learner.collect_events()]

    assert "QuestProgressed" not in names
    assert "QuestCompleted" not in names
    instance = learner.quest_instances["remediation:att-1"]
    assert instance["status"] == "assigned"
    assert instance["progress"] == 0
    assert learner.xp_total > 0


def test_remediation_completion_after_deadline_does_not_complete_and_expires_lazily():
    # Lazy expiry: the first completion after the deadline marks the quest expired inside
    # complete_lesson, stamped with the deadline (as the sweep does).
    learner = _remediation_learner()

    learner.complete_lesson(
        "c-late",
        "lesson-1",
        occurred_at=ASSIGNED + timedelta(days=7, hours=1),
        timezone="UTC",
        completion_kind="remediation",
    )
    events = learner.collect_events()
    names = [type(e).__name__ for e in events]

    assert "QuestCompleted" not in names
    assert "QuestProgressed" not in names
    assert "QuestExpired" in names
    expired = [e for e in events if type(e).__name__ == "QuestExpired"]
    assert expired[0].expired_at == (ASSIGNED + timedelta(days=7)).isoformat()  # the deadline
    assert learner.quest_instances["remediation:att-1"]["status"] == "expired"
    assert "quest_completion:remediation:att-1" not in learner.completed_quest_ids
    assert learner.xp_total > 0


def test_remediation_completion_without_an_active_quest_still_earns_xp():
    learner = Learner()
    learner.collect_events()

    learner.complete_lesson(
        "c-1",
        "lesson-1",
        occurred_at=OCCURRED,
        timezone="UTC",
        completion_kind="remediation",
    )
    names = [type(e).__name__ for e in learner.collect_events()]

    assert learner.xp_total > 0
    assert learner.quest_instances == {}
    assert learner.assigned_quest_ids == set()
    assert learner.completed_quest_ids == set()
    assert not any(name.startswith("Quest") for name in names)


def test_remediation_completion_earns_full_xp_with_no_decay(monkeypatch):
    from src.state_machine.rules import _REGISTRY, load_decay_rulesets

    for version, ruleset in load_decay_rulesets().items():
        monkeypatch.setitem(_REGISTRY, version, ruleset)
    learner = Learner()
    for index in range(4):  # push the lesson well into the decay curve
        learner.complete_lesson(
            f"c-{index}",
            "lesson-1",
            occurred_at=OCCURRED + timedelta(minutes=index),
            timezone="UTC",
            rule_version="v2",
            completion_kind="lesson",
        )
    learner.collect_events()
    before = learner.xp_total

    learner.complete_lesson(
        "c-rem",
        "lesson-1",
        occurred_at=OCCURRED + timedelta(hours=1),
        timezone="UTC",
        rule_version="v2",
        completion_kind="remediation",
    )
    awards = [e for e in learner.collect_events() if isinstance(e, Learner.XPAwarded)]

    assert len(awards) == 1
    assert awards[0].repeat_index == 0
    assert awards[0].xp_amount == 100
    assert learner.xp_total - before == 100
