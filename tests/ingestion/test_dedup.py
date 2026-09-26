"""AT-1: Duplicate Event Dedup (M1)

Given learner L1 submits lesson.completed for l_492 and receives XP_awarded = E (first attempt, N = 0), when the identical event (same dedup_key) is re-ingested within the 7-day window, then it is silently discarded and XP_total remains unchanged.
"""
import pytest

@pytest.mark.skip(reason="Not yet implemented")
def test_duplicate_event_dedup():
    pass
