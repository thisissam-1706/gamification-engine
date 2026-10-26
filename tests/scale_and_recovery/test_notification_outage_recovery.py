"""Tests for notification outage and DLQ recovery drain (M5, M8)."""

from src.scale_and_recovery.outage import NotificationOutageHarness


def test_notification_outage_and_recovery_drain(tmp_path):
    db_path = str(tmp_path / "notif_outage.db")
    harness = NotificationOutageHarness(db_path)
    try:
        num_proposals = 25
        result = harness.run_outage_and_recovery(num_proposals=num_proposals)

        # 1. Outage phase: 100% of messages captured to DLQ
        assert result.dlq_routed_count == num_proposals
        assert result.dlq_capture_rate == 1.0

        # 2. Recovery phase: all backlog messages drained
        assert result.recovery_drained_count == num_proposals
        assert result.recovery_drain_duration_sec > 0.0
        assert result.drain_throughput_msgs_per_sec > 10.0

        # 3. Invariants: zero duplicates, zero lost
        assert result.duplicate_deliveries_count == 0
        assert result.passed is True
        assert result.violations == []

        # 4. Storage invariant: 0 unresolved entries left in DLQ
        unresolved = harness.store.get_dlq_entries(resolved=False)
        assert len(unresolved) == 0

    finally:
        harness.close()
