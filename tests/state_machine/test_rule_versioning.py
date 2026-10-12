"""AT-5: Historical Rule Versioning (M2)

Given xp_awarded was stamped with rule_version = v1 when E = 100, when the global rule config changes to E = 150 and replay occurs, then the historical event still yields XP_awarded = 100, not 150.
"""
from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.projection import rebuild_learner_state
from src.state_machine.rules import RULESETS, RuleSet, XP_AMOUNTS


def test_historical_reward_replay_uses_persisted_amount_after_rule_update(tmp_path):
    """Replay folds the old reward fact; it does not consult a new rule."""
    app = LearnerApplication(env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": str(tmp_path / "events.db")})
    try:
        learner = Learner()
        learner.complete_lesson("c-v1", "l1", rule_version="v1")
        app.save(learner)
        events = list(app.events.get(learner.id))

        XP_AMOUNTS["v1"] = 150  # Current configuration changes after award.
        replayed = rebuild_learner_state(events)
        awards = [event for event in events if isinstance(event, Learner.XPAwarded)]
        assert replayed.xp_total == 100
        assert len(awards) == 1
        assert awards[0].xp_amount == 100
        assert awards[0].reward_id == "xp:c-v1"
        assert awards[0].rule_version == "v1"
    finally:
        XP_AMOUNTS["v1"] = 100
        app.close()


def test_v2_ruleset_changes_xp_only_when_selected():
    original = RULESETS.get("v2")
    RULESETS["v2"] = RuleSet(
        xp_amount=150,
        quest_targets={"lesson_starter_v1": 3},
        badge_threshold=5,
        badge_id="week_warrior_v2",
    )
    try:
        v1 = Learner()
        v2 = Learner()
        v1.complete_lesson("v1", "lesson", activity_date="2026-10-01")
        v2.complete_lesson(
            "v2", "lesson", activity_date="2026-10-01", rule_version="v2"
        )
        assert v1.xp_total == 100
        assert v2.xp_total == 150
    finally:
        if original is None:
            del RULESETS["v2"]
        else:
            RULESETS["v2"] = original


def test_v2_ruleset_changes_badge_threshold_only_when_selected():
    original = RULESETS.get("v2")
    RULESETS["v2"] = RuleSet(
        xp_amount=150,
        quest_targets={"lesson_starter_v1": 3},
        badge_threshold=5,
        badge_id="week_warrior_v2",
    )
    try:
        v1 = Learner()
        v2 = Learner()
        for index in range(5):
            day = f"2026-10-0{index + 1}"
            v1.complete_lesson(f"v1-{index}", "lesson", activity_date=day)
            v2.complete_lesson(
                f"v2-{index}", "lesson", activity_date=day, rule_version="v2"
            )
        assert "week_warrior_v1" not in v1.badges_issued
        assert "week_warrior_v2" in v2.badges_issued
    finally:
        if original is None:
            del RULESETS["v2"]
        else:
            RULESETS["v2"] = original


def test_v2_ruleset_changes_quest_target_only_when_selected():
    original = RULESETS.get("v2")
    RULESETS["v2"] = RuleSet(
        xp_amount=150,
        quest_targets={"lesson_starter_v1": 2},
        badge_threshold=5,
        badge_id="week_warrior_v2",
    )
    try:
        v1 = Learner()
        v2 = Learner()
        v1.assign_quest(
            "lesson_starter_v1",
            assigned_at="2026-10-01T00:00:00+00:00",
            definition_version="v1",
        )
        v2.assign_quest(
            "lesson_starter_v1",
            assigned_at="2026-10-01T00:00:00+00:00",
            definition_version="v2",
        )
        for index in range(2):
            day = f"2026-10-0{index + 1}"
            v1.complete_lesson(f"v1-{index}", "lesson", activity_date=day)
            v2.complete_lesson(
                f"v2-{index}", "lesson", activity_date=day, rule_version="v2"
            )
        assert v1.quest_instances["quest:lesson_starter_v1"]["status"] == "assigned"
        assert v2.quest_instances["quest:lesson_starter_v1"]["status"] == "completed"
    finally:
        if original is None:
            del RULESETS["v2"]
        else:
            RULESETS["v2"] = original


def test_v1_events_replay_with_v1_facts_after_v2_registration():
    original = RULESETS.get("v2")
    RULESETS["v2"] = RuleSet(
        xp_amount=150,
        quest_targets={"lesson_starter_v1": 3},
        badge_threshold=5,
        badge_id="week_warrior_v2",
    )
    try:
        learner = Learner()
        for index in range(5):
            learner.complete_lesson(
                f"v1-replay-{index}",
                "lesson",
                activity_date=f"2026-10-0{index + 1}",
                rule_version="v1",
            )
        replayed = rebuild_learner_state(list(learner.collect_events()))
        assert replayed.xp_total == learner.xp_total == 500
        assert replayed.badges_issued == learner.badges_issued
    finally:
        if original is None:
            del RULESETS["v2"]
        else:
            RULESETS["v2"] = original
