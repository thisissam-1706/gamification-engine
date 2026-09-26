"""AT-7: Leaderboard Reconciliation (M3)

Given Redis and Postgres diverge on L1’s score by a dropped event, when the daily reconciliation job runs, then it applies a differential ZADD correction (not a full flush) and resolves any tie using occurred_at of the first event reaching that score, then subject_id.
"""
import pytest

@pytest.mark.skip(reason="Not yet implemented")
def test_leaderboard_reconciliation():
    pass
