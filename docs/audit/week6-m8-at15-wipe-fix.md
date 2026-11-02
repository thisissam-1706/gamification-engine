# Week 6 / M8 / AT-15 — make the projection wipe real

Status: **Step 3 complete (see section 12 onward).** Step 1: failing tests added, no `src/` change.
Git rule in force: no state-changing git commands were run.

## 1. Changes made

- Created this file and `tests/scale_and_recovery/test_rebuild_wipe_is_real.py` (3 tests). Nothing under `src/` changed.
- Fixture: `WorkloadProfile(population_size=8, cohort_count=2, events_per_learner=6, seed=42)`, `tmp_path` DBs, epoch `2026-W42`.

## 2. Before / after

No behaviour change yet. Problem as reported: `simulate_projection_wipe()` deletes only the leaderboard SQLite file and keeps the same
`LearnerApplication`; `capture_snapshot()` derives XP/streak/freezes with `rebuild_learner_state()` both times, so they cannot differ.

## 3. Tests added

1. `test_wipe_empties_leaderboard_before_replay` — pre snapshot, wipe, snapshot again with no replay; every learner must have `cohort_rank`/`global_rank` None and `cohort_score` 0.
   I also added a sanity precondition on the pre-wipe snapshot (`any(cohort_rank is not None)`) so the test cannot pass vacuously.
2. `test_wipe_reopens_application` — `harness.app` after the wipe must be a different object, and the old object must be closed/unusable (`old_app.events.get(uuid)` must raise).
3. `test_skipping_replay_fails_verification` — sha256 of pre vs post snapshot (same scheme as `run_rebuild_verification`) must differ.

## 4. Command and full output

```bash
python -m pytest tests/scale_and_recovery/test_rebuild_wipe_is_real.py -v
```

```text
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0 -- /home/sampadram/miniconda3/bin/python
cachedir: .pytest_cache
rootdir: /home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine
configfile: pyproject.toml
plugins: anyio-4.12.1, asyncio-1.4.1
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 3 items

tests/scale_and_recovery/test_rebuild_wipe_is_real.py::test_wipe_empties_leaderboard_before_replay FAILED [ 33%]
tests/scale_and_recovery/test_rebuild_wipe_is_real.py::test_wipe_reopens_application FAILED [ 66%]
tests/scale_and_recovery/test_rebuild_wipe_is_real.py::test_skipping_replay_fails_verification FAILED [100%]

=================================== FAILURES ===================================
_________________ test_wipe_empties_leaderboard_before_replay __________________

tmp_path = PosixPath('/tmp/pytest-of-sampadram/pytest-49/test_wipe_empties_leaderboard_0')

    def test_wipe_empties_leaderboard_before_replay(tmp_path):
        harness, workload, learner_uuids = _ingested_harness(tmp_path)
        try:
            pre = harness.capture_snapshot(workload, learner_uuids)
>           assert any(s.cohort_rank is not None for s in pre.values())
E           assert False
E            +  where False = any(<generator object test_wipe_empties_leaderboard_before_replay.<locals>.<genexpr> at 0x7c2f7ad55be0>)

tests/scale_and_recovery/test_rebuild_wipe_is_real.py:37: AssertionError
________________________ test_wipe_reopens_application _________________________

tmp_path = PosixPath('/tmp/pytest-of-sampadram/pytest-49/test_wipe_reopens_application0')

    def test_wipe_reopens_application(tmp_path):
        harness, workload, learner_uuids = _ingested_harness(tmp_path)
        try:
            old_app = harness.app
            assert old_app is not None
            some_uuid = next(iter(learner_uuids.values()))
    
            harness.simulate_projection_wipe()
    
            assert harness.app is not None
>           assert harness.app is not old_app
E           assert <src.state_machine.learner.LearnerApplication object at 0x7c2f7ad6d310> is not <src.state_machine.learner.LearnerApplication object at 0x7c2f7ad6d310>
E            +  where <src.state_machine.learner.LearnerApplication object at 0x7c2f7ad6d310> = <src.scale_and_recovery.rebuild.FullStateRebuildHarness object at 0x7c2f7ad6d090>.app

tests/scale_and_recovery/test_rebuild_wipe_is_real.py:61: AssertionError
___________________ test_skipping_replay_fails_verification ____________________

tmp_path = PosixPath('/tmp/pytest-of-sampadram/pytest-49/test_skipping_replay_fails_ver0')

    def test_skipping_replay_fails_verification(tmp_path):
        harness, workload, learner_uuids = _ingested_harness(tmp_path)
        try:
            pre = harness.capture_snapshot(workload, learner_uuids)
            harness.simulate_projection_wipe()
            post = harness.capture_snapshot(workload, learner_uuids)
    
>           assert _checksum(pre) != _checksum(post)
E           AssertionError: assert '6ff8d82551e05e39769654bc69c0f34f14a24b496844b660617cfecf69c10607' != '6ff8d82551e05e39769654bc69c0f34f14a24b496844b660617cfecf69c10607'
E            +  where '6ff8d82551e05e39769654bc69c0f34f14a24b496844b660617cfecf69c10607' = _checksum({'learner_0000': LearnerSnapshot(learner_id='learner_0000', xp_total=600, streak=3, freezes=0, activity_dates=('2026-10-12', '2026-10-13', '2026-10-14'), cohort_id='cohort_a', cohort_score=0, cohort_rank=None, cohort_first_reached_at=None, global_score=0, global_rank=None, global_first_reached_at=None), 'learner_0001': LearnerSnapshot(learner_id='learner_0001', xp_total=600, streak=4, freezes=0, activity_dates=('2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15'), cohort_id='cohort_b', cohort_score=0, cohort_rank=None, cohort_first_reached_at=None, global_score=0, global_rank=None, global_first_reached_at=None), 'learner_0002': LearnerSnapshot(learner_id='learner_0002', xp_total=700, streak=4, freezes=0, activity_dates=('2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15'), cohort_id='cohort_a', cohort_score=0, cohort_rank=None, cohort_first_reached_at=None, global_score=0, global_rank=None, global_first_reached_at=None), 'learner_0003': LearnerSnapshot(learner_id='learner_0003', xp_total=600, streak=4, freezes=0, activity_dates=('2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15'), cohort_id='cohort_b', cohort_score=0, cohort_rank=None, cohort_first_reached_at=None, global_score=0, global_rank=None, global_first_reached_at=None), ...})
E            +  and   '6ff8d82551e05e39769654bc69c0f34f14a24b496844b660617cfecf69c10607' = _checksum({'learner_0000': LearnerSnapshot(learner_id='learner_0000', xp_total=600, streak=3, freezes=0, activity_dates=('2026-10-12', '2026-10-13', '2026-10-14'), cohort_id='cohort_a', cohort_score=0, cohort_rank=None, cohort_first_reached_at=None, global_score=0, global_rank=None, global_first_reached_at=None), 'learner_0001': LearnerSnapshot(learner_id='learner_0001', xp_total=600, streak=4, freezes=0, activity_dates=('2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15'), cohort_id='cohort_b', cohort_score=0, cohort_rank=None, cohort_first_reached_at=None, global_score=0, global_rank=None, global_first_reached_at=None), 'learner_0002': LearnerSnapshot(learner_id='learner_0002', xp_total=700, streak=4, freezes=0, activity_dates=('2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15'), cohort_id='cohort_a', cohort_score=0, cohort_rank=None, cohort_first_reached_at=None, global_score=0, global_rank=None, global_first_reached_at=None), 'learner_0003': LearnerSnapshot(learner_id='learner_0003', xp_total=600, streak=4, freezes=0, activity_dates=('2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15'), cohort_id='cohort_b', cohort_score=0, cohort_rank=None, cohort_first_reached_at=None, global_score=0, global_rank=None, global_first_reached_at=None), ...})

tests/scale_and_recovery/test_rebuild_wipe_is_real.py:82: AssertionError
=========================== short test summary info ============================
FAILED tests/scale_and_recovery/test_rebuild_wipe_is_real.py::test_wipe_empties_leaderboard_before_replay
FAILED tests/scale_and_recovery/test_rebuild_wipe_is_real.py::test_wipe_reopens_application
FAILED tests/scale_and_recovery/test_rebuild_wipe_is_real.py::test_skipping_replay_fails_verification
============================== 3 failed in 0.75s ===============================
```

## 5. Analysis of the failures

- Test 2 fails for the expected reason: `harness.app` is the same object after the wipe.
- **Unexpected finding (tests 1 and 3):** the pre-wipe snapshot already has `cohort_rank=None`, `cohort_score=0`, `global_rank=None` for every learner.
  So the leaderboard half of AT-15 is also vacuous today (empty == empty), not just XP/streak/freezes.
  - Read-only probe (not committed; run from the scratchpad): after `ingest_workload`, `leaderboard_entries` has 16 rows, e.g.
    `('cohort','cohort_a','2026-W42','a43aba5c-2f98-4c6f-9c86-cba8d886861e',700)`; `ingest_workload` maps `learner_0000 -> 2415c630-cf70-4dd5-9cc3-c07267e308a5`.
  - Cause (hypothesis, strongly supported): the projection keys entries by the aggregate **UUID** string, but `capture_snapshot` calls
    `service.get_learner_rank(..., lid)` with the workload id (`learner_0000`), so lookups always miss. Fix candidate: pass `str(uid)`.
  - Consequence for the fix step: after fixing the lookup, tests 1 and 3 should fail/pass for the right reasons; test 1's precondition will then hold.
- Test 3 currently fails only because pre == post (both all-empty), which is the wrong reason in the sense that the snapshot is blind to ranks, not that the wipe is ineffective. Flagged, not hidden.

## 6. Assumptions and uncertainty

- Assumed `old_app.events.get(uuid)` raises after `LearnerApplication.close()` (SQLite recorder pool closed). Unverified until the fix lands; if it does not raise, test 2's "unusable" check needs a different probe.
- Did not check `LeaderboardService.get_learner_rank` internals beyond the probe; the UUID-vs-lid cause is inferred from stored rows.
- Only the new file was run; the existing `tests/integration/test_full_state_rebuild.py` was not run.

---

# Step 2 — make the tests pass (only `src/scale_and_recovery/rebuild.py` changed)

Status: **Step 2 complete: the 3 Step 1 tests pass, AT-15 passes, full suite green.** No state-changing git commands were run.

## 7. What the harness opens (derived/projection files)

- Authoritative: events DB (`events_db_path`, eventsourcing SQLite). **Never deleted.**
- Derived: leaderboard DB (`leaderboard_db_path`, `SQLiteLeaderboardStore`, opened with `PRAGMA journal_mode=WAL`), so `<path>-wal` and `<path>-shm` sidecars can exist.
- Nothing else: `LeaderboardProjection` and `LeaderboardService` only wrap the store; no other file is created by the harness.
- Wipe deletes: `leaderboard.db`, `leaderboard.db-wal`, `leaderboard.db-shm` (each if present). (Closing the connection normally checkpoints/removes the sidecars; deleting them is a safeguard.)

## 8. Changes made (`src/scale_and_recovery/rebuild.py`)

1. `simulate_projection_wipe()`: closes store and app, sets all four handles to None, deletes the three leaderboard files, then `_open_application()` (NEW `LearnerApplication`, same events DB path) and `_open_read_model()` (new empty store/projection/service).
   Added helpers `_open_application` / `_open_read_model`, also used by `initialize_live_system()`.
2. `capture_snapshot(workload, learner_uuids, source="replay")`: `source="aggregate"` reads `xp_total`, `new_streak`, `freezes_remaining`, `sorted(activity_dates)` from `app.repository.get(uuid)`;
   `source="replay"` uses `rebuild_learner_state()` on `app.events.get(uuid)`. Unknown value raises `ValueError`.
   Aggregate field names exist as expected on `Learner` (`xp_total`, `new_streak`, `freezes_remaining`, `activity_dates`), so no stop was needed.
3. `run_rebuild_verification()`: pre-wipe snapshot uses `source="aggregate"`; `execute_full_rebuild()` post snapshot uses `source="replay"` on the fresh app.
4. **Also changed (needed for the tests, not in your list):** rank lookups now use `str(uuid)` instead of the workload id, because the leaderboard keys entries by aggregate UUID (see section 5). Without it ranks were None before the wipe and tests 1/3 could not pass for the right reason.

## 9. Before / after

- Before: wipe kept the same app; pre and post XP/streak/freezes came from the same fold; ranks were always None (lookup by wrong id). AT-15 compared empty with empty on ranks.
- After: wipe reopens the app on the same events DB; pre uses the live aggregate, post uses a fold on a fresh app; ranks are real (UUID lookup) and are only non-empty post-wipe if replay actually rebuilt them.

## 10. Commands and full output

```bash
python -m pytest tests/scale_and_recovery -v
```

```text
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0 -- /home/sampadram/miniconda3/bin/python
cachedir: .pytest_cache
rootdir: /home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine
configfile: pyproject.toml
plugins: anyio-4.12.1, asyncio-1.4.1
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 12 items

tests/scale_and_recovery/test_experiment_reproduction.py::test_experiment_reproduction_is_bit_identical PASSED [  8%]
tests/scale_and_recovery/test_latency_benchmarks.py::test_percentile_calculation_ordering PASSED [ 16%]
tests/scale_and_recovery/test_latency_benchmarks.py::test_latency_measurement_satisfies_standard_sla PASSED [ 25%]
tests/scale_and_recovery/test_latency_benchmarks.py::test_latency_measurement_reports_violations_on_strict_threshold PASSED [ 33%]
tests/scale_and_recovery/test_notification_outage_recovery.py::test_notification_outage_and_recovery_drain PASSED [ 41%]
tests/scale_and_recovery/test_rebuild_wipe_is_real.py::test_wipe_empties_leaderboard_before_replay PASSED [ 50%]
tests/scale_and_recovery/test_rebuild_wipe_is_real.py::test_wipe_reopens_application PASSED [ 58%]
tests/scale_and_recovery/test_rebuild_wipe_is_real.py::test_skipping_replay_fails_verification PASSED [ 66%]
tests/scale_and_recovery/test_workload_generator.py::test_workload_generator_identical_output_for_same_seed PASSED [ 75%]
tests/scale_and_recovery/test_workload_generator.py::test_workload_generator_different_output_for_different_seeds PASSED [ 83%]
tests/scale_and_recovery/test_workload_generator.py::test_workload_generator_noise_ratios_are_bounded PASSED [ 91%]
tests/scale_and_recovery/test_workload_generator.py::test_workload_event_timestamps_are_aware_utc_iso PASSED [100%]

============================== 12 passed in 0.94s ==============================
```

```bash
python -m pytest tests/integration/test_full_state_rebuild.py -v
```

```text
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0 -- /home/sampadram/miniconda3/bin/python
cachedir: .pytest_cache
rootdir: /home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine
configfile: pyproject.toml
plugins: anyio-4.12.1, asyncio-1.4.1
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 1 item

tests/integration/test_full_state_rebuild.py::test_full_state_rebuild PASSED [100%]

============================== 1 passed in 1.22s ===============================
```

```bash
python -m pytest
```

```text
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.12.1, asyncio-1.4.1
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 771 items / 1 deselected / 770 selected

tests/abuse/test_abuse_rules.py .........................                [  3%]
tests/abuse/test_abuse_rules_v2.py ..................................... [  8%]
.........................                                                [ 11%]
tests/abuse/test_detectors.py ..................................         [ 15%]
tests/abuse/test_evaluation.py ...............                           [ 17%]
tests/abuse/test_guardrail.py .............                              [ 19%]
tests/abuse/test_point_farming.py ...                                    [ 19%]
tests/abuse/test_receive_timing.py ...............                       [ 21%]
tests/abuse/test_review_misuse.py ..................                     [ 24%]
tests/abuse/test_signal_store_and_service.py ..............              [ 25%]
tests/experiments/test_assignment_vs_exposure.py s                       [ 25%]
tests/experiments/test_deterministic_assignment.py ........s             [ 27%]
tests/experiments/test_exp007_yaml_matches_code.py ........              [ 28%]
tests/experiments/test_metrics.py .                                      [ 28%]
tests/experiments/test_metrics_hardening.py ............................ [ 31%]
....                                                                     [ 32%]
tests/experiments/test_metrics_review_probes.py ......                   [ 33%]
tests/ingestion/test_completed_quest_lesson.py .....                     [ 33%]
tests/ingestion/test_dedup.py ...                                        [ 34%]
tests/ingestion/test_dedup_key.py ........                               [ 35%]
tests/ingestion/test_event_id_binding.py ........                        [ 36%]
tests/ingestion/test_evidence_migration_concurrency.py ..                [ 36%]
tests/ingestion/test_ingestion_service.py ............                   [ 38%]
tests/ingestion/test_late_window.py ..............                       [ 40%]
tests/ingestion/test_late_window_behaviour.py .                          [ 40%]
tests/ingestion/test_lesson_completed_contract.py .....                  [ 40%]
tests/ingestion/test_provenance.py ......                                [ 41%]
tests/ingestion/test_rule_digest.py ....                                 [ 42%]
tests/ingestion/test_rule_selection.py ........                          [ 43%]
tests/ingestion/test_rule_version_change.py ..                           [ 43%]
tests/ingestion/test_trace.py .....                                      [ 44%]
tests/integration/test_full_state_rebuild.py .                           [ 44%]
tests/interventions/test_checkpoint_attempt_ingestion.py ............... [ 46%]
....                                                                     [ 46%]
tests/interventions/test_learning_remediation.py ...                     [ 47%]
tests/interventions/test_quest_expiry.py ......................          [ 49%]
tests/interventions/test_remediation_end_to_end.py ..........            [ 51%]
tests/interventions/test_remediation_policy.py ..................        [ 53%]
tests/interventions/test_remediation_quest.py ....................       [ 56%]
tests/interventions/test_remediation_service.py ...............          [ 58%]
tests/leaderboard/test_atomic_awards.py .........                        [ 59%]
tests/leaderboard/test_checkpoint_offsets.py .........                   [ 60%]
tests/leaderboard/test_cohort_constraints.py ..........                  [ 61%]
tests/leaderboard/test_cohort_lifecycle_reconcile.py .........           [ 62%]
tests/leaderboard/test_deterministic_ties.py ..........                  [ 64%]
tests/leaderboard/test_leaderboard_e2e.py .....                          [ 64%]
tests/leaderboard/test_leaderboard_latency.py ..                         [ 65%]
tests/leaderboard/test_leaderboard_projection.py .....                   [ 65%]
tests/leaderboard/test_leaderboard_reconciliation.py ..                  [ 65%]
tests/leaderboard/test_leaderboard_recovery.py .......                   [ 66%]
tests/leaderboard/test_rehydrate_cohort.py ......                        [ 67%]
tests/notifications/test_crash_safety.py .....                           [ 68%]
tests/notifications/test_duplicate_proposals.py .                        [ 68%]
tests/notifications/test_notification_fatigue.py ...                     [ 68%]
tests/notifications/test_notification_slice_e2e.py .....                 [ 69%]
tests/notifications/test_opt_out_enforcement.py .                        [ 69%]
tests/notifications/test_quiet_hour_delay.py ..                          [ 69%]
tests/notifications/test_release_replay.py .......                       [ 70%]
tests/notifications/test_retry_dlq.py ...                                [ 71%]
tests/packaging/test_wheel_contents.py .                                 [ 71%]
tests/scale_and_recovery/test_experiment_reproduction.py .               [ 71%]
tests/scale_and_recovery/test_latency_benchmarks.py ...                  [ 71%]
tests/scale_and_recovery/test_notification_outage_recovery.py .          [ 71%]
tests/scale_and_recovery/test_rebuild_wipe_is_real.py ...                [ 72%]
tests/scale_and_recovery/test_workload_generator.py ....                 [ 72%]
tests/simulator/test_pilot_report.py .................                   [ 75%]
tests/simulator/test_pilot_streams.py ..............                     [ 76%]
tests/simulator/test_population.py .........                             [ 78%]
tests/simulator/test_run_pilot.py ...........                            [ 79%]
tests/state_machine/test_active_state_path.py ..                         [ 79%]
tests/state_machine/test_atomic_rebuild.py ..                            [ 80%]
tests/state_machine/test_badge_idempotency.py .                          [ 80%]
tests/state_machine/test_freeze_eligibility.py ......                    [ 80%]
tests/state_machine/test_late_event_streak.py ..                         [ 81%]
tests/state_machine/test_live_vs_replay.py ...........                   [ 82%]
tests/state_machine/test_out_of_order.py .                               [ 82%]
tests/state_machine/test_persisted_reload_evidence.py ...........        [ 84%]
tests/state_machine/test_projection_rebuild.py .                         [ 84%]
tests/state_machine/test_quest.py .......                                [ 85%]
tests/state_machine/test_quest_reader.py ............                    [ 86%]
tests/state_machine/test_replay_checksum.py .                            [ 86%]
tests/state_machine/test_replay_no_decide.py ..                          [ 87%]
tests/state_machine/test_rule_versioning.py .....                        [ 87%]
tests/state_machine/test_rules_immutable.py ............................ [ 91%]
                                                                         [ 91%]
tests/state_machine/test_sqlite_spike.py ..                              [ 91%]
tests/state_machine/test_streak_contract.py .......                      [ 92%]
tests/state_machine/test_streak_reader.py ..............                 [ 94%]
tests/state_machine/test_streak_updates.py .........                     [ 95%]
tests/state_machine/test_xp_decay.py ..................................  [100%]

================ 768 passed, 2 skipped, 1 deselected in 30.13s =================
```

## 11. Assumptions and uncertainty

- `app.repository.get(uuid)` still reconstructs the aggregate by applying its stored events (no snapshotting is configured that I checked), so "live aggregate" means the aggregate's own apply-methods, which are independent code from `rebuild_learner_state()`. It is not a long-lived in-memory object. This is the closest available reading of "live aggregate fields".
- `activity_dates` is compared as a sorted tuple on both sides (aggregate keeps a set).
- Test 2's "old app is unusable" check passes, which confirms `events.get` raises after `close()`; resolves the Step 1 assumption.
- I did not mutation-test AT-15 (e.g. skipping replay inside `run_rebuild_verification`); `test_skipping_replay_fails_verification` covers the snapshot-level equivalent only.

---

# Step 3 — corruption detection, event counts, docs

Status: **Step 3 complete; full suite green (770 passed, 2 skipped, 1 deselected).** No state-changing git commands were run.

## 12. Changes made

1. `tests/scale_and_recovery/test_rebuild_wipe_is_real.py`: added `test_corrupted_score_after_replay_is_reported_as_mismatch`.
   It wraps `execute_full_rebuild` on the harness instance (test-only, no src hook): after the real replay it calls `store.upsert_score` on learner_0000's cohort entry with `score + 1000`
   and re-captures the post snapshot, then runs `run_rebuild_verification` and asserts `bit_identical is False`, differing checksums, and exactly one `State mismatch for learner_0000` entry.
   Also added `test_result_reports_domain_event_count_and_notification_count` (clean run: `bit_identical`, both counts > 0, `domain_event_count` in `to_dict()`).
2. `src/scale_and_recovery/rebuild.py`: `RebuildResult.domain_event_count` added next to `total_events` (also in `to_dict()`); docstring now says `total_events` is "notification-log entries replayed" and `domain_event_count` is the sum of stored events across all learner aggregates (computed on the fresh app after rebuild).
3. `tests/integration/test_full_state_rebuild.py`: only the comment on the `> 500` line changed to "Notification-log entries replayed". Logic untouched.
4. `docs/traceability-matrix.md`: new M8/AT-15 clarification block (the doc had no AT-15 clarification before; added one beside the M6 clarification, acceptance wording unchanged) and a 2026-11-02 changelog line.
5. `README.md`: M8 row in the milestone table reworded; 2026-11-02 changelog line added.

## 13. Real numbers used in the docs

Measured with the AT-15 fixture (15 learners, 2 cohorts, 12 events/learner, 5% duplicates, 5% late, seed 42): workload `total_events` = 198 input events;
`total_events` (notification entries replayed) = 612; `domain_event_count` = 612 (each stored domain event produces one notification here, so they coincide). The wipe tests use 8 learners.

## 14. Commands and full output

```bash
python -m pytest tests/scale_and_recovery/test_rebuild_wipe_is_real.py -v
```

```text
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0 -- /home/sampadram/miniconda3/bin/python
cachedir: .pytest_cache
rootdir: /home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine
configfile: pyproject.toml
plugins: anyio-4.12.1, asyncio-1.4.1
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 5 items

tests/scale_and_recovery/test_rebuild_wipe_is_real.py::test_wipe_empties_leaderboard_before_replay PASSED [ 20%]
tests/scale_and_recovery/test_rebuild_wipe_is_real.py::test_wipe_reopens_application PASSED [ 40%]
tests/scale_and_recovery/test_rebuild_wipe_is_real.py::test_skipping_replay_fails_verification PASSED [ 60%]
tests/scale_and_recovery/test_rebuild_wipe_is_real.py::test_corrupted_score_after_replay_is_reported_as_mismatch PASSED [ 80%]
tests/scale_and_recovery/test_rebuild_wipe_is_real.py::test_result_reports_domain_event_count_and_notification_count PASSED [100%]

============================== 5 passed in 1.58s ===============================
```

```bash
python -m pytest
```

```text
============================= test session starts ==============================
platform linux -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
rootdir: /home/sampadram/Sampadram/IITH/SEM 7/DATGC/project/gamification-engine
configfile: pyproject.toml
testpaths: tests
plugins: anyio-4.12.1, asyncio-1.4.1
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 773 items / 1 deselected / 772 selected

tests/abuse/test_abuse_rules.py .........................                [  3%]
tests/abuse/test_abuse_rules_v2.py ..................................... [  8%]
.........................                                                [ 11%]
tests/abuse/test_detectors.py ..................................         [ 15%]
tests/abuse/test_evaluation.py ...............                           [ 17%]
tests/abuse/test_guardrail.py .............                              [ 19%]
tests/abuse/test_point_farming.py ...                                    [ 19%]
tests/abuse/test_receive_timing.py ...............                       [ 21%]
tests/abuse/test_review_misuse.py ..................                     [ 23%]
tests/abuse/test_signal_store_and_service.py ..............              [ 25%]
tests/experiments/test_assignment_vs_exposure.py s                       [ 25%]
tests/experiments/test_deterministic_assignment.py ........s             [ 27%]
tests/experiments/test_exp007_yaml_matches_code.py ........              [ 28%]
tests/experiments/test_metrics.py .                                      [ 28%]
tests/experiments/test_metrics_hardening.py ............................ [ 31%]
....                                                                     [ 32%]
tests/experiments/test_metrics_review_probes.py ......                   [ 33%]
tests/ingestion/test_completed_quest_lesson.py .....                     [ 33%]
tests/ingestion/test_dedup.py ...                                        [ 34%]
tests/ingestion/test_dedup_key.py ........                               [ 35%]
tests/ingestion/test_event_id_binding.py ........                        [ 36%]
tests/ingestion/test_evidence_migration_concurrency.py ..                [ 36%]
tests/ingestion/test_ingestion_service.py ............                   [ 38%]
tests/ingestion/test_late_window.py ..............                       [ 39%]
tests/ingestion/test_late_window_behaviour.py .                          [ 40%]
tests/ingestion/test_lesson_completed_contract.py .....                  [ 40%]
tests/ingestion/test_provenance.py ......                                [ 41%]
tests/ingestion/test_rule_digest.py ....                                 [ 41%]
tests/ingestion/test_rule_selection.py ........                          [ 43%]
tests/ingestion/test_rule_version_change.py ..                           [ 43%]
tests/ingestion/test_trace.py .....                                      [ 43%]
tests/integration/test_full_state_rebuild.py .                           [ 44%]
tests/interventions/test_checkpoint_attempt_ingestion.py ............... [ 45%]
....                                                                     [ 46%]
tests/interventions/test_learning_remediation.py ...                     [ 46%]
tests/interventions/test_quest_expiry.py ......................          [ 49%]
tests/interventions/test_remediation_end_to_end.py ..........            [ 51%]
tests/interventions/test_remediation_policy.py ..................        [ 53%]
tests/interventions/test_remediation_quest.py ....................       [ 55%]
tests/interventions/test_remediation_service.py ...............          [ 57%]
tests/leaderboard/test_atomic_awards.py .........                        [ 59%]
tests/leaderboard/test_checkpoint_offsets.py .........                   [ 60%]
tests/leaderboard/test_cohort_constraints.py ..........                  [ 61%]
tests/leaderboard/test_cohort_lifecycle_reconcile.py .........           [ 62%]
tests/leaderboard/test_deterministic_ties.py ..........                  [ 63%]
tests/leaderboard/test_leaderboard_e2e.py .....                          [ 64%]
tests/leaderboard/test_leaderboard_latency.py ..                         [ 64%]
tests/leaderboard/test_leaderboard_projection.py .....                   [ 65%]
tests/leaderboard/test_leaderboard_reconciliation.py ..                  [ 65%]
tests/leaderboard/test_leaderboard_recovery.py .......                   [ 66%]
tests/leaderboard/test_rehydrate_cohort.py ......                        [ 67%]
tests/notifications/test_crash_safety.py .....                           [ 68%]
tests/notifications/test_duplicate_proposals.py .                        [ 68%]
tests/notifications/test_notification_fatigue.py ...                     [ 68%]
tests/notifications/test_notification_slice_e2e.py .....                 [ 69%]
tests/notifications/test_opt_out_enforcement.py .                        [ 69%]
tests/notifications/test_quiet_hour_delay.py ..                          [ 69%]
tests/notifications/test_release_replay.py .......                       [ 70%]
tests/notifications/test_retry_dlq.py ...                                [ 70%]
tests/packaging/test_wheel_contents.py .                                 [ 71%]
tests/scale_and_recovery/test_experiment_reproduction.py .               [ 71%]
tests/scale_and_recovery/test_latency_benchmarks.py ...                  [ 71%]
tests/scale_and_recovery/test_notification_outage_recovery.py .          [ 71%]
tests/scale_and_recovery/test_rebuild_wipe_is_real.py .....              [ 72%]
tests/scale_and_recovery/test_workload_generator.py ....                 [ 72%]
tests/simulator/test_pilot_report.py .................                   [ 75%]
tests/simulator/test_pilot_streams.py ..............                     [ 76%]
tests/simulator/test_population.py .........                             [ 78%]
tests/simulator/test_run_pilot.py ...........                            [ 79%]
tests/state_machine/test_active_state_path.py ..                         [ 79%]
tests/state_machine/test_atomic_rebuild.py ..                            [ 80%]
tests/state_machine/test_badge_idempotency.py .                          [ 80%]
tests/state_machine/test_freeze_eligibility.py ......                    [ 80%]
tests/state_machine/test_late_event_streak.py ..                         [ 81%]
tests/state_machine/test_live_vs_replay.py ...........                   [ 82%]
tests/state_machine/test_out_of_order.py .                               [ 82%]
tests/state_machine/test_persisted_reload_evidence.py ...........        [ 84%]
tests/state_machine/test_projection_rebuild.py .                         [ 84%]
tests/state_machine/test_quest.py .......                                [ 85%]
tests/state_machine/test_quest_reader.py ............                    [ 86%]
tests/state_machine/test_replay_checksum.py .                            [ 86%]
tests/state_machine/test_replay_no_decide.py ..                          [ 87%]
tests/state_machine/test_rule_versioning.py .....                        [ 87%]
tests/state_machine/test_rules_immutable.py ............................ [ 91%]
                                                                         [ 91%]
tests/state_machine/test_sqlite_spike.py ..                              [ 91%]
tests/state_machine/test_streak_contract.py .......                      [ 92%]
tests/state_machine/test_streak_reader.py ..............                 [ 94%]
tests/state_machine/test_streak_updates.py .........                     [ 95%]
tests/state_machine/test_xp_decay.py ..................................  [100%]

================ 770 passed, 2 skipped, 1 deselected in 29.64s =================
```

## 15. Assumptions and uncertainty

- The corruption test depends on `harness.execute_full_rebuild` being called through `self`, so instance-level wrapping works; it would break if `run_rebuild_verification` bound the method differently.
- I did not run the corruption test against a deliberately broken harness (e.g. a verification that ignores ranks); it is expected to fail then because `bit_identical` would be True, but that was not executed.
- Ranks of other learners in the victim's cohort may also shift after the corruption; the test only requires the victim to be reported exactly once, not that peers are absent.
- `total_events == domain_event_count` (612) in this fixture, so the doc cannot show them differing; they could diverge if an event type were ever excluded from the notification log.
- The traceability doc had no existing AT-15 clarification to "update", so I added one; tell me if you wanted it folded into the acceptance text instead.
