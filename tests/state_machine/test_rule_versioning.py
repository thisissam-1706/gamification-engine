"""AT-5: Historical Rule Versioning (M2)

Given xp_awarded was stamped with rule_version = v1 when E = 100, when the global rule config changes to E = 150 and replay occurs, then the historical event still yields XP_awarded = 100, not 150.
"""
from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.projection import rebuild_learner_state
from src.state_machine.rules import XP_AMOUNTS


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
