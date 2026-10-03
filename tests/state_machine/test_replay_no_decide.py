"""Step 2: Prove that replay/rebuild does NOT call decision logic.

Two tests:
1. Monkeypatch calculate_streak, advance_quest_progress (quest_target_for),
   and xp_amount_for to raise AssertionError, then rebuild a learner from
   stored events. State must match pre-rebuild state.
2. Count stored events before and after a rebuild. Assert zero new events.
"""

from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.projection import (
    LearnerCompletionProjection,
    LearnerStreakProjection,
    LearnerQuestProjection,
    rebuild_learner_state,
)
from src.state_machine import rules as rules_module


def test_replay_does_not_call_decision_functions(tmp_path, monkeypatch):
    """Monkeypatching decision functions to raise still allows full rebuild.

    This proves that replay/rebuild reads stored event fields only.
    Decision functions (calculate_streak, xp_amount_for, quest_target_for)
    are never invoked during replay.
    """
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events.db"),
        }
    )
    try:
        learner = Learner()
        learner.assign_quest("lesson_starter_v1")
        # Day 1: first activity
        learner.complete_lesson("c1", "l1", activity_date="2026-09-01")
        learner.acquire_freeze("f1")
        # Day 2
        learner.complete_lesson("c2", "l2", activity_date="2026-09-02")
        # Day 3 - completes quest (3 lessons)
        learner.complete_lesson("c3", "l3", activity_date="2026-09-03")
        app.save(learner)
        learner_id = learner.id

        # Capture pre-rebuild state from the aggregate
        pre = app.repository.get(learner_id)
        pre_xp = pre.xp_total              # 3 * 100 = 300
        pre_streak = pre.current_streak     # 3 consecutive days
        pre_freezes = pre.freezes_remaining # 1 acquired, 0 consumed
        pre_completed_ids = set(pre.completed_quest_ids)

        # Capture event-based rebuilt state before monkeypatch
        events = list(app.events.get(learner_id))
        pre_rebuild = rebuild_learner_state(events)

        # -- Monkeypatch all decision functions to raise --
        def _boom(*args, **kwargs):
            raise AssertionError("Decision function called during replay!")

        monkeypatch.setattr(rules_module, "calculate_streak", _boom)
        monkeypatch.setattr(rules_module, "xp_amount_for", _boom)
        monkeypatch.setattr(rules_module, "quest_target_for", _boom)

        # Rebuild from stored events should NOT call any of the above
        post_rebuild = rebuild_learner_state(events)

        # Hand-calculated expected values:
        # 3 lessons * 100 XP each = 300 XP total
        # 3 consecutive days (Sep 1,2,3) = streak 3
        # 1 freeze acquired, 0 gaps = 1 freeze remaining
        # Quest lesson_starter_v1 target=3, completed after 3 lessons
        assert post_rebuild.xp_total == 300
        assert post_rebuild.current_streak == 3
        assert post_rebuild.freezes_remaining == 1
        assert post_rebuild.completed_quests == ("lesson_starter_v1",)
        assert post_rebuild == pre_rebuild

        # Projection rebuilds also must not call decision functions
        completion_proj = LearnerCompletionProjection()
        completion_proj.rebuild(events)
        assert completion_proj.completed_lesson_counts[learner_id] == 3

        streak_proj = LearnerStreakProjection()
        streak_proj.rebuild(events)
        summary = streak_proj.streak_summaries[learner_id]
        assert summary.current_streak == 3
        assert summary.freezes_remaining == 1

        quest_proj = LearnerQuestProjection()
        quest_proj.rebuild(events)
        quest_summary = quest_proj.get_summary(learner_id)
        assert quest_summary.completed_quests == ("lesson_starter_v1",)
        assert quest_summary.quest_progress == {"lesson_starter_v1": 3}
    finally:
        app.close()


def test_rebuild_creates_zero_new_events(tmp_path):
    """A full rebuild from stored events must not create any new domain events.

    Counts business events before and after rebuild, asserts they are identical.
    """
    app = LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events.db"),
        }
    )
    try:
        learner = Learner()
        learner.assign_quest("lesson_starter_v1")
        learner.complete_lesson("c1", "l1", activity_date="2026-09-01")
        learner.acquire_freeze("f1")
        learner.complete_lesson("c2", "l2", activity_date="2026-09-02")
        learner.complete_lesson("c3", "l3", activity_date="2026-09-03")
        app.save(learner)

        events_before = list(app.events.get(learner.id))
        count_before = len(events_before)

        # Count each business event type before rebuild
        xp_count_before = sum(1 for e in events_before if isinstance(e, Learner.XPAwarded))
        streak_count_before = sum(1 for e in events_before if isinstance(e, Learner.StreakUpdated))
        quest_prog_before = sum(1 for e in events_before if isinstance(e, Learner.QuestProgressed))
        quest_comp_before = sum(1 for e in events_before if isinstance(e, Learner.QuestCompleted))

        # Perform rebuild
        rebuild_learner_state(events_before)

        # Re-read events from store — no new events should have been created
        events_after = list(app.events.get(learner.id))
        count_after = len(events_after)

        assert count_after == count_before, (
            f"Rebuild created {count_after - count_before} new events"
        )

        # Verify each event type count is unchanged
        xp_count_after = sum(1 for e in events_after if isinstance(e, Learner.XPAwarded))
        streak_count_after = sum(1 for e in events_after if isinstance(e, Learner.StreakUpdated))
        quest_prog_after = sum(1 for e in events_after if isinstance(e, Learner.QuestProgressed))
        quest_comp_after = sum(1 for e in events_after if isinstance(e, Learner.QuestCompleted))

        # Hand-calculated counts:
        # 3 XPAwarded (one per lesson)
        # 3 StreakUpdated (one per dated lesson)
        # 3 QuestProgressed (one per lesson, quest target=3)
        # 1 QuestCompleted (quest completed on 3rd lesson)
        assert xp_count_before == 3
        assert streak_count_before == 3
        assert quest_prog_before == 3
        assert quest_comp_before == 1

        assert xp_count_after == xp_count_before
        assert streak_count_after == streak_count_before
        assert quest_prog_after == quest_prog_before
        assert quest_comp_after == quest_comp_before
    finally:
        app.close()
