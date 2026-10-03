"""AT-15: Full State Rebuild (M2, M7, M8)

Given a simulated database wipe for the full synthetic population, when the event log is replayed from offset 0, then all learners’ {XP, StreakCount, Freezes} match their pre-wipe values exactly, and leaderboard/cohort projections rebuild to the same ranks.
"""
import pytest

@pytest.mark.skip(reason="Multi-learner full-state orchestration is not implemented.")
def test_full_state_rebuild():
    pass
