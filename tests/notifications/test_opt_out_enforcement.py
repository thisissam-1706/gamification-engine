"""AT-11: Opt-Out Enforcement (M5)

Given L1 has opted out of push, when a candidate is generated for L1, then Notification Failure is emitted with failure_reason = opt_out, not a delivery attempt.
"""
import pytest

@pytest.mark.skip(reason="Notifications are outside this OSS spike.")
def test_opt_out_enforcement():
    pass
