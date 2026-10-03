"""AT-6: Point Farming / Decay Cap (M6)

Given E = 100, K = 20, learner completes lesson l_492 for the 3rd time (N = 2), when XP is calculated, then XP_awarded = max(0, 0.5(100) − 20(1)) = 30 and if this pattern pushes a cohort’s decayed-XP share above 15% of weekly total, the policy is flagged.
"""
import pytest

@pytest.mark.skip(reason="Abuse detection is outside this OSS spike.")
def test_point_farming():
    pass
