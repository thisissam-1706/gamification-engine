"""Tests for raw-log experiment reproduction (M7, M8)."""

from src.scale_and_recovery.experiment_repro import verify_experiment_reproduction


def test_experiment_reproduction_is_bit_identical():
    learners = [f"learner_{i:04d}" for i in range(40)]
    result = verify_experiment_reproduction(learners, exp_id="exp_007", seed=42)

    assert result.bit_identical is True
    assert result.checksum_first == result.checksum_second
    assert result.mastery_report_first == result.mastery_report_second

    # Report must have populated metrics
    assert result.total_attempts > 0
    assert result.total_completions > 0
    assert result.mastery_report_first["assigned"] == 40
    assert result.mastery_report_first["mastered"] >= 0
