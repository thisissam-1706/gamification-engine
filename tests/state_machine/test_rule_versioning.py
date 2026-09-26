"""AT-5: Historical Rule Versioning (M2)

Given xp_awarded was stamped with rule_version = v1 when E = 100, when the global rule config changes to E = 150 and replay occurs, then the historical event still yields XP_awarded = 100, not 150.
"""
import pytest

@pytest.mark.skip(reason="Not yet implemented")
def test_historical_rule_versioning():
    pass
