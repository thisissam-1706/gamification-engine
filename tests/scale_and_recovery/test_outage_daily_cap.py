"""The outage harness must not exceed the daily cap: no learner gets more than 5 proposals, so the whole DLQ drains."""

import pytest

from src.scale_and_recovery.outage import NotificationOutageHarness


@pytest.mark.parametrize("num_proposals", [100, 500])
def test_outage_recovery_drains_every_entry(tmp_path, num_proposals):
    harness = NotificationOutageHarness(str(tmp_path / "notifications.db"))
    try:
        result = harness.run_outage_and_recovery(num_proposals=num_proposals)
        unresolved = harness.store.get_dlq_entries(resolved=False)
    finally:
        harness.close()

    assert result.violations == []
    assert result.passed is True
    assert result.dlq_routed_count == num_proposals
    assert len(unresolved) == 0
