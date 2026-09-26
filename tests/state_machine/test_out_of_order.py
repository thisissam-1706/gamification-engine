"""AT-3: Out-of-Order Within 72h (M2)

Given learner L1’s last processed event has occurred_at = 2026-09-22, when an event with occurred_at = 2026-09-21 arrives at ingested_at = 2026-09-24 (within 72h), then a micro-replay is triggered from 2026-09-21 forward.
"""
import pytest

@pytest.mark.skip(reason="Not yet implemented")
def test_out_of_order():
    pass
