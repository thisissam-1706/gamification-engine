"""Weekly cohort builder: build_weekly_cohorts(application, learner_ids, epoch, ...).

Written before the implementation (src/leaderboard/cohort_builder.py does not exist yet).

Decided convention: lesson_id is "<module_id>/<lesson>"; a learner's module is the text before the
FIRST "/" of the lesson_id; a lesson_id with no "/" has no module.

Assumed contract (see docs/audit/m3-cohort-step1-tests.md):
- prior-week lessons = stored ``LessonCompleted`` events whose UTC ``occurred_at`` falls in the ISO
  week before ``epoch``;
- module = the module with the most prior-week completions, ties to the lexicographically smallest
  module id, and "general" when there is no prefixed lesson in that week;
- returns ``dict[learner_id -> cohort_key]``; the key contains the activity-volume cohort id
  (``assign_activity_volume_cohort``) and the module cohort id (``assign_module_cohort``);
- the builder records the assignment: one ``CohortAssigned`` event per newly assigned learner with
  ``cohort_id`` == the returned key, ``membership_epoch`` == epoch and
  ``grouping_rule_version`` == "cohort_builder_v1".
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from src.leaderboard.cohorts import assign_activity_volume_cohort, assign_module_cohort
from src.state_machine.learner import Learner, LearnerApplication

EPOCH = "2026-W42"  # Mon 2026-10-12 .. Sun 2026-10-18
NEXT_EPOCH = "2026-W43"
OLDER_WEEK_START = datetime(2026, 9, 28, 9, tzinfo=timezone.utc)  # 2026-W40
PRIOR_WEEK_START = datetime(2026, 10, 5, 9, tzinfo=timezone.utc)  # 2026-W41
THIS_WEEK_START = datetime(2026, 10, 12, 9, tzinfo=timezone.utc)  # 2026-W42
REFERENCE = datetime(2026, 10, 12, 8, tzinfo=timezone.utc)
NEXT_REFERENCE = datetime(2026, 10, 19, 8, tzinfo=timezone.utc)


def _build(application, learner_ids, epoch=EPOCH, reference_time=REFERENCE, **kwargs):
    # Imported here so a missing module fails each test with a clear ImportError.
    from src.leaderboard.cohort_builder import build_weekly_cohorts

    return build_weekly_cohorts(
        application,
        learner_ids,
        epoch,
        min_size=1,
        reference_time=reference_time,
        # These tests compute expected ids with the assign_* defaults, so they pin them explicitly.
        activity_buckets=10,
        module_buckets=5,
        **kwargs,
    )


def _app(tmp_path) -> LearnerApplication:
    return LearnerApplication(
        env={
            "PERSISTENCE_MODULE": "eventsourcing.sqlite",
            "SQLITE_DBNAME": str(tmp_path / "events.db"),
        }
    )


def _learner(application, lessons, *, tz="UTC", extras=False) -> str:
    """Stores a learner with the given (occurred_at, lesson_id) completions."""
    learner = Learner()
    for index, (occurred_at, lesson_id) in enumerate(lessons):
        learner.complete_lesson(
            f"cmp-{index}", lesson_id, occurred_at=occurred_at.isoformat(), timezone=tz
        )
    if extras:  # attributes that must NOT influence the cohort
        learner.assign_quest(
            "lesson_starter_v1",
            assignment_id="quest-x",
            assigned_at="2026-09-01T00:00:00Z",
            deadline_at="2026-12-01T00:00:00Z",
        )
        learner.acquire_freeze("freeze-x", activity_date="2026-10-05")
    application.save(learner)
    return str(learner.id)


def _week(start: datetime, count: int, module: str = "math", offset: int = 0) -> list:
    """`count` completions of "<module>/l<i>" spread over the week starting at `start`."""
    return [
        (start + timedelta(hours=3 * (offset + i)), f"{module}/l{offset + i}")
        for i in range(count)
    ]


def _expect(learner_id: str, epoch: str, lessons: int, module: str) -> tuple[str, str]:
    return (
        assign_activity_volume_cohort(learner_id, epoch, lessons),
        assign_module_cohort(learner_id, epoch, module),
    )


def _assert_key(cohorts, learner_id, epoch, lessons, module, tier):
    activity, mod = _expect(learner_id, epoch, lessons, module)
    key = cohorts[learner_id]
    assert f"_{tier}_" in activity  # the expectation itself names the tier we mean
    assert f"_{module}_" in mod
    assert activity in key and mod in key, (key, activity, mod)


# --- a. prior-week count and tiers --------------------------------------------------


def test_a_tiers_from_prior_week_lesson_counts(tmp_path):
    app = _app(tmp_path)
    try:
        tiers = {2: "casual", 3: "regular", 9: "regular", 10: "intensive"}
        ids = {n: _learner(app, _week(PRIOR_WEEK_START, n)) for n in tiers}

        cohorts = _build(app, list(ids.values()))

        for n, tier in tiers.items():
            _assert_key(cohorts, ids[n], EPOCH, n, "math", tier)
    finally:
        app.close()


def test_a_only_the_iso_week_before_the_epoch_counts(tmp_path):
    app = _app(tmp_path)
    try:
        lessons = (
            _week(PRIOR_WEEK_START, 2)  # W41: counts
            + _week(OLDER_WEEK_START, 5, module="physics")  # W40: ignored
            + _week(THIS_WEEK_START, 4, module="physics")  # W42 (the epoch itself): ignored
            + [(datetime(2026, 10, 11, 23, 59, tzinfo=timezone.utc), "math/edge")]  # W41 Sunday: counts
        )
        learner_id = _learner(app, lessons)

        cohorts = _build(app, [learner_id])

        _assert_key(cohorts, learner_id, EPOCH, 3, "math", "regular")  # 2 + 1 edge
    finally:
        app.close()


# --- b. module choice ---------------------------------------------------------------


def test_b_module_is_the_one_with_most_prior_week_completions(tmp_path):
    app = _app(tmp_path)
    try:
        learner_id = _learner(
            app,
            _week(PRIOR_WEEK_START, 3, module="physics")
            + _week(PRIOR_WEEK_START, 1, module="math", offset=3),
        )

        cohorts = _build(app, [learner_id])

        _assert_key(cohorts, learner_id, EPOCH, 4, "physics", "regular")
    finally:
        app.close()


def test_b_module_tie_goes_to_the_lexicographically_smallest(tmp_path):
    app = _app(tmp_path)
    try:
        # "physics" is stored first, "chem" and "math" tie with it at 2 each; "chem" must win.
        learner_id = _learner(
            app,
            _week(PRIOR_WEEK_START, 2, module="physics")
            + _week(PRIOR_WEEK_START, 2, module="math", offset=2)
            + _week(PRIOR_WEEK_START, 2, module="chem", offset=4),
        )

        cohorts = _build(app, [learner_id])

        _assert_key(cohorts, learner_id, EPOCH, 6, "chem", "regular")
    finally:
        app.close()


def test_b_module_is_the_text_before_the_first_slash_and_only_prior_week_counts(tmp_path):
    app = _app(tmp_path)
    try:
        learner_id = _learner(
            app,
            [(PRIOR_WEEK_START, "math_101/l3/extra"), (PRIOR_WEEK_START + timedelta(hours=1), "math_101/l4")]
            + _week(OLDER_WEEK_START, 6, module="physics"),  # heavier, but not in the prior week
        )

        cohorts = _build(app, [learner_id])

        _assert_key(cohorts, learner_id, EPOCH, 2, "math_101", "casual")
    finally:
        app.close()


def test_b_no_prefixed_lessons_means_module_general(tmp_path):
    app = _app(tmp_path)
    try:
        no_slash = _learner(
            app,
            [(PRIOR_WEEK_START + timedelta(hours=i), f"lesson-{i}") for i in range(4)],
        )  # four completions, none with a module prefix

        cohorts = _build(app, [no_slash])

        _assert_key(cohorts, no_slash, EPOCH, 4, "general", "regular")  # still counted for the tier
    finally:
        app.close()


# --- c. key = tier + module ---------------------------------------------------------


def test_c_cohort_key_combines_tier_and_module(tmp_path):
    app = _app(tmp_path)
    try:
        learner_id = _learner(app, _week(PRIOR_WEEK_START, 5, module="physics"))

        key = _build(app, [learner_id])[learner_id]

        activity, module = _expect(learner_id, EPOCH, 5, "physics")
        assert activity in key and module in key
        assert "_regular_" in key and "_physics_" in key
    finally:
        app.close()


# --- d. deterministic ---------------------------------------------------------------


def test_d_same_input_gives_same_cohorts(tmp_path):
    app = _app(tmp_path)
    try:
        ids = [
            _learner(app, _week(PRIOR_WEEK_START, n, module=m))
            for n, m in [(1, "math"), (4, "math"), (12, "physics"), (0, "physics")]
        ]

        first = _build(app, ids)
        second = _build(app, ids)  # idempotent: nothing changes the answer
        reordered = _build(app, list(reversed(ids)))

        assert first == second == reordered
        assert set(first) == set(ids)
    finally:
        app.close()


# --- e. only lesson counts and the module prefix matter -----------------------------


def test_e_other_learner_attributes_do_not_change_the_cohort(tmp_path):
    app = _app(tmp_path)
    try:
        plain = _learner(app, _week(PRIOR_WEEK_START, 4))
        # Same prior-week count and module; different timezone, a quest, a freeze, 30 older
        # lessons and different lesson names after the "math/" prefix.
        busy = _learner(
            app,
            [(t, f"math/other-name-{i}") for i, (t, _) in enumerate(_week(PRIOR_WEEK_START, 4))]
            + _week(OLDER_WEEK_START, 30, module="math"),
            tz="Asia/Kolkata",
            extras=True,
        )

        cohorts = _build(app, [plain, busy])

        for learner_id in (plain, busy):
            _assert_key(cohorts, learner_id, EPOCH, 4, "math", "regular")
    finally:
        app.close()


# --- f. no prior-week data ----------------------------------------------------------


def test_f_learner_without_prior_week_data_is_casual_and_general(tmp_path):
    app = _app(tmp_path)
    try:
        older_history = _learner(app, _week(OLDER_WEEK_START, 6, module="physics"))
        no_lessons = _learner(app, [])

        cohorts = _build(app, [older_history, no_lessons])

        for learner_id in (older_history, no_lessons):
            _assert_key(cohorts, learner_id, EPOCH, 0, "general", "casual")
    finally:
        app.close()


# --- g. the next epoch can change the cohort ----------------------------------------


def test_g_next_epoch_can_give_a_different_cohort(tmp_path):
    app = _app(tmp_path)
    try:
        # W41: 2 math lessons (casual/math for W42). W42: 10 physics lessons (intensive/physics for W43).
        learner_id = _learner(
            app, _week(PRIOR_WEEK_START, 2) + _week(THIS_WEEK_START, 10, module="physics")
        )

        this_week = _build(app, [learner_id])
        next_week = _build(app, [learner_id], epoch=NEXT_EPOCH, reference_time=NEXT_REFERENCE)

        _assert_key(this_week, learner_id, EPOCH, 2, "math", "casual")
        _assert_key(next_week, learner_id, NEXT_EPOCH, 10, "physics", "intensive")
        assert this_week[learner_id] != next_week[learner_id]
    finally:
        app.close()


# --- h. recorded grouping rule version ----------------------------------------------


def test_h_assignment_records_grouping_rule_version_cohort_builder_v1(tmp_path):
    app = _app(tmp_path)
    try:
        learner_id = _learner(app, _week(PRIOR_WEEK_START, 3))

        cohorts = _build(app, [learner_id])

        assigned = [
            e
            for e in app.events.get(UUID(learner_id))
            if type(e).__name__ == "CohortAssigned"
        ]
        assert len(assigned) == 1
        assert assigned[0].grouping_rule_version == "cohort_builder_v1"
        assert assigned[0].membership_epoch == EPOCH
        assert assigned[0].cohort_id == cohorts[learner_id]
        # And the aggregate state agrees.
        stored = app.repository.get(UUID(learner_id)).cohort_memberships[EPOCH]
        assert stored["grouping_rule_version"] == "cohort_builder_v1"
        assert stored["cohort_id"] == cohorts[learner_id]
    finally:
        app.close()
