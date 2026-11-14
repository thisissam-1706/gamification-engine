"""M7 step 1: assignment + eligibility recording (exp_007). Written before the implementation.

Assumed new module ``src.experiments.store``:

* ``ExperimentStore(database)`` (SQLite, like ``AbuseSignalStore``; ``.connection``, ``.close()``)
  creating tables ``experiment_eligibility`` and ``experiment_decisions``.
* ``assign_and_record_eligibility(application, store, learner_ids, prefs_by_learner, exp_id,
  assigned_at)``: assigns each learner via ``assign_learner_to_experiment`` and inserts ONE
  ``experiment_eligibility`` row per learner (both arms), first record wins, with columns
  exp_id, learner_id, variant, assigned_at, eligible, reason, opted_out_push,
  opted_out_streak_risk, timezone, tenure_days. Rows are read here with raw SQL, so no read API
  is assumed. Booleans may be stored as 0/1; they are compared with ``bool()``.

Assumptions (not fixed by the brief): assigned_at is stored as ``assigned_at.isoformat()``;
tenure_days is whole days (assigned_at - learner creation); the 3-day rule is
``tenure < timedelta(days=3)`` as in streak_risk.MIN_TENURE; missing-prefs learners are
otherwise tenure-eligible and have NULL opt-out/timezone columns.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from src.experiments.service import assign_and_record_eligibility
from src.experiments.store import ExperimentStore
from src.notifications.models import ChannelType, LearnerPreferences, NotificationCategory
from src.state_machine.learner import Learner
from tests.experiments.support import (
    EXP_ID,
    open_persisted,
    stored_event_names,
    uuid_for_variant,
)

ASSIGNED_AT = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
OLD = ASSIGNED_AT - timedelta(days=10)
COLUMNS = (
    "exp_id, learner_id, variant, assigned_at, eligible, reason, "
    "opted_out_push, opted_out_streak_risk, timezone, tenure_days"
)


def _make(application, variant: str, created: datetime, nth: int = 0) -> str:
    learner = Learner._create(
        Learner.Created, id=uuid_for_variant(variant, EXP_ID, nth=nth), timestamp=created
    )
    application.save(learner)
    return str(learner.id)


def _prefs(learner_id: str, **kwargs) -> LearnerPreferences:
    return LearnerPreferences(learner_id, timezone=kwargs.pop("tz", "Asia/Kolkata"), **kwargs)


def _rows(store: ExperimentStore) -> dict[str, dict]:
    cursor = store.connection.execute(f"SELECT {COLUMNS} FROM experiment_eligibility")
    names = [c.strip() for c in COLUMNS.split(",")]
    return {r[1]: dict(zip(names, r)) for r in cursor.fetchall()}


def _count(store: ExperimentStore) -> int:
    return store.connection.execute("SELECT COUNT(*) FROM experiment_eligibility").fetchone()[0]


def _run(application, store, ids, prefs, assigned_at=ASSIGNED_AT):
    return assign_and_record_eligibility(application, store, ids, prefs, EXP_ID, assigned_at)


def _assigned_count(application, learner_id: str) -> int:
    return stored_event_names(application, learner_id).count("ExperimentAssigned")


# 1 ---------------------------------------------------------------------------------------------


def test_store_creates_both_tables():
    store = ExperimentStore(":memory:")
    try:
        names = {
            r[0]
            for r in store.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"experiment_eligibility", "experiment_decisions"} <= names
    finally:
        store.close()


# 2 ---------------------------------------------------------------------------------------------


def test_every_learner_gets_one_row_for_both_arms_with_stored_variant(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        a = _make(application, "variant_a", OLD)
        b = _make(application, "variant_b", OLD)
        prefs = {
            a: _prefs(a, tz="Asia/Kolkata"),
            b: _prefs(b, tz="America/New_York"),
        }
        store = ExperimentStore(":memory:")
        try:
            _run(application, store, [a, b], prefs)
            rows = _rows(store)
            assert set(rows) == {a, b}
            assert _count(store) == 2
            for learner_id, expected_variant in ((a, "variant_a"), (b, "variant_b")):
                learner = application.repository.get(UUID(learner_id))
                stored = learner.experiment_assignments[EXP_ID]
                row = rows[learner_id]
                assert row["variant"] == stored["variant"] == expected_variant
                assert row["exp_id"] == EXP_ID
                assert row["assigned_at"] == ASSIGNED_AT.isoformat()
                assert bool(row["eligible"]) is True
                assert row["reason"] is None
                assert not row["opted_out_push"]
                assert not row["opted_out_streak_risk"]
                assert row["timezone"] == prefs[learner_id].timezone
                assert row["tenure_days"] == 10
                assert _assigned_count(application, learner_id) == 1
        finally:
            store.close()


# 3 ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("variant", ["variant_a", "variant_b"])
def test_short_tenure_is_excluded_in_both_arms(tmp_path, variant):
    with open_persisted(tmp_path) as (application, _):
        young = _make(application, variant, ASSIGNED_AT - timedelta(days=2, hours=23))
        store = ExperimentStore(":memory:")
        try:
            _run(application, store, [young], {young: _prefs(young)})
            row = _rows(store)[young]
            assert row["variant"] == variant
            assert bool(row["eligible"]) is False
            assert row["reason"] == "excluded_tenure"
            assert row["tenure_days"] == 2
        finally:
            store.close()


def test_exactly_three_days_of_tenure_is_eligible(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        learner = _make(application, "variant_a", ASSIGNED_AT - timedelta(days=3))
        store = ExperimentStore(":memory:")
        try:
            _run(application, store, [learner], {learner: _prefs(learner)})
            row = _rows(store)[learner]
            assert bool(row["eligible"]) is True
            assert row["reason"] is None
            assert row["tenure_days"] == 3
        finally:
            store.close()


@pytest.mark.parametrize("variant", ["variant_a", "variant_b"])
@pytest.mark.parametrize(
    "opt_out",
    [
        {"opted_out_channels": {ChannelType.PUSH}},
        {"opted_out_categories": {NotificationCategory.STREAK_RISK}},
    ],
    ids=["push_channel", "streak_risk_category"],
)
def test_opt_out_is_excluded_in_both_arms(tmp_path, variant, opt_out):
    with open_persisted(tmp_path) as (application, _):
        learner = _make(application, variant, OLD)
        store = ExperimentStore(":memory:")
        try:
            _run(application, store, [learner], {learner: _prefs(learner, **opt_out)})
            row = _rows(store)[learner]
            assert bool(row["eligible"]) is False
            assert row["reason"] == "excluded_opted_out"
            assert bool(row["opted_out_push"]) is ("opted_out_channels" in opt_out)
            assert bool(row["opted_out_streak_risk"]) is ("opted_out_categories" in opt_out)
        finally:
            store.close()


@pytest.mark.parametrize("variant", ["variant_a", "variant_b"])
def test_missing_preferences_row_is_still_written_and_assigned(tmp_path, variant):
    with open_persisted(tmp_path) as (application, _):
        learner = _make(application, variant, OLD)
        store = ExperimentStore(":memory:")
        try:
            _run(application, store, [learner], {})
            row = _rows(store)[learner]
            assert row["variant"] == variant
            assert bool(row["eligible"]) is False
            assert row["reason"] == "missing_preferences"
            assert _assigned_count(application, learner) == 1
        finally:
            store.close()


# 4 ---------------------------------------------------------------------------------------------


def test_rerun_adds_no_second_event_or_row(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        ids = [_make(application, "variant_a", OLD), _make(application, "variant_b", OLD)]
        prefs = {i: _prefs(i) for i in ids}
        store = ExperimentStore(":memory:")
        try:
            _run(application, store, ids, prefs)
            before = _rows(store)
            _run(application, store, ids, prefs)
            assert _count(store) == 2
            assert _rows(store) == before
            for learner_id in ids:
                assert _assigned_count(application, learner_id) == 1
        finally:
            store.close()


def test_changed_prefs_between_runs_do_not_change_stored_row(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        learner = _make(application, "variant_a", OLD)
        store = ExperimentStore(":memory:")
        try:
            _run(application, store, [learner], {learner: _prefs(learner)})
            first = _rows(store)[learner]
            assert bool(first["eligible"]) is True

            opted_out = _prefs(
                learner,
                tz="Europe/London",
                opted_out_channels={ChannelType.PUSH},
                opted_out_categories={NotificationCategory.STREAK_RISK},
            )
            _run(
                application,
                store,
                [learner],
                {learner: opted_out},
                assigned_at=ASSIGNED_AT + timedelta(days=1),
            )
            assert _count(store) == 1
            assert _rows(store)[learner] == first
            assert _assigned_count(application, learner) == 1
        finally:
            store.close()


# 5 ---------------------------------------------------------------------------------------------


def test_rows_survive_close_and_reopen_of_file_store(tmp_path):
    db = str(tmp_path / "experiments.db")
    with open_persisted(tmp_path) as (application, _):
        a = _make(application, "variant_a", OLD)
        b = _make(application, "variant_b", ASSIGNED_AT - timedelta(days=1))
        prefs = {a: _prefs(a), b: _prefs(b)}
        store = ExperimentStore(db)
        _run(application, store, [a, b], prefs)
        before = _rows(store)
        store.close()

        reopened = ExperimentStore(db)
        try:
            assert _rows(reopened) == before
            assert len(before) == 2
            _run(application, reopened, [a, b], prefs)
            assert _count(reopened) == 2
        finally:
            reopened.close()


# 6 ---------------------------------------------------------------------------------------------


def test_naive_assigned_at_raises_and_writes_nothing(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        ids = [_make(application, "variant_a", OLD), _make(application, "variant_b", OLD)]
        prefs = {i: _prefs(i) for i in ids}
        store = ExperimentStore(":memory:")
        try:
            with pytest.raises(ValueError):
                _run(application, store, ids, prefs, assigned_at=datetime(2026, 10, 20, 12, 0))
            assert _count(store) == 0
            for learner_id in ids:
                assert _assigned_count(application, learner_id) == 0
        finally:
            store.close()


# Edge cases -----------------------------------------------------------------------------------


def test_empty_learner_ids_returns_zeros_and_writes_nothing(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        store = ExperimentStore(":memory:")
        try:
            summary = _run(application, store, [], {})
            assert (
                summary.newly_assigned,
                summary.already_assigned,
                summary.eligible,
                summary.excluded,
            ) == (0, 0, 0, 0)
            assert _count(store) == 0
        finally:
            store.close()


def test_unknown_learner_raises_and_earlier_learners_stay_recorded(tmp_path):
    from uuid import uuid4

    with open_persisted(tmp_path) as (application, _):
        a = _make(application, "variant_a", OLD)
        b = _make(application, "variant_b", OLD)
        c = _make(application, "variant_a", OLD, nth=1)
        missing = str(uuid4())
        prefs = {i: _prefs(i) for i in (a, b, c, missing)}
        store = ExperimentStore(":memory:")
        try:
            with pytest.raises(Exception) as excinfo:
                _run(application, store, [a, b, missing, c], prefs)
            assert not isinstance(excinfo.value, (AssertionError, ValueError))
            assert set(_rows(store)) == {a, b}
            assert _assigned_count(application, a) == 1
            assert _assigned_count(application, b) == 1
            assert _assigned_count(application, c) == 0
        finally:
            store.close()


def test_one_second_short_of_three_days_is_excluded(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        learner = _make(
            application, "variant_a", ASSIGNED_AT - timedelta(days=3) + timedelta(seconds=1)
        )
        store = ExperimentStore(":memory:")
        try:
            _run(application, store, [learner], {learner: _prefs(learner)})
            row = _rows(store)[learner]
            assert bool(row["eligible"]) is False
            assert row["reason"] == "excluded_tenure"
        finally:
            store.close()


def test_email_only_opt_out_stays_eligible(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        learner = _make(application, "variant_b", OLD)
        store = ExperimentStore(":memory:")
        try:
            prefs = _prefs(learner, opted_out_channels={ChannelType.EMAIL})
            _run(application, store, [learner], {learner: prefs})
            row = _rows(store)[learner]
            assert bool(row["eligible"]) is True
            assert row["reason"] is None
            assert not row["opted_out_push"]
        finally:
            store.close()


def test_tenure_is_measured_at_assigned_at_not_call_time(tmp_path):
    with open_persisted(tmp_path) as (application, _):
        created = datetime(2026, 10, 1, tzinfo=timezone.utc)
        learner = _make(application, "variant_a", created)
        store = ExperimentStore(":memory:")
        try:
            # Real wall-clock time is long past three days after `created`; assigned_at is not.
            _run(
                application,
                store,
                [learner],
                {learner: _prefs(learner)},
                assigned_at=created + timedelta(days=1),
            )
            row = _rows(store)[learner]
            assert bool(row["eligible"]) is False
            assert row["reason"] == "excluded_tenure"
            assert row["tenure_days"] == 1
        finally:
            store.close()
