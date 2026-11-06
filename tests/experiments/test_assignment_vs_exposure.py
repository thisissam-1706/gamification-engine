"""AT-13: Assignment vs. Exposure (M7)

Given L1 is assigned to variant_a for exp_007, when L1 has not yet opened the app, then only Experiment Assignment exists. Experiment Exposure is emitted only once L1 actually views the treatment surface.
"""
from datetime import datetime, timedelta
from uuid import UUID

from src.experiments.service import assign_learner_to_experiment
from tests.experiments.support import (
    ASSIGNED_AT,
    EXP_ID,
    TREATMENT_SURFACE,
    create_learner,
    exposure_state,
    ingest_lesson_completed,
    ingest_surface_viewed,
    lesson_completed_envelope,
    open_persisted,
    stored_event_names,
    surface_viewed_envelope,
)


def test_assignment_vs_exposure(tmp_path):
    with open_persisted(tmp_path) as (application, evidence):
        learner_id = create_learner(application, "variant_a")

        # 1. Assign.
        assert assign_learner_to_experiment(application, learner_id, EXP_ID, ASSIGNED_AT) is True

        # 2. L1 has not yet opened the app: assignment only.
        names = stored_event_names(application, learner_id)
        assert names.count("ExperimentAssigned") == 1
        assert names.count("ExperimentExposed") == 0
        learner = application.repository.get(UUID(learner_id))
        assert learner.first_experiment_exposure(EXP_ID) is None

        # 3. Other activity is not an exposure.
        lesson = lesson_completed_envelope(learner_id, "cmp-1", ASSIGNED_AT + timedelta(minutes=30))
        assert ingest_lesson_completed(application, evidence, lesson).status == "accepted"
        names = stored_event_names(application, learner_id)
        assert names.count("LessonCompleted") == 1
        assert names.count("ExperimentExposed") == 0

        # 4. Viewing the treatment surface is the exposure.
        view_time = ASSIGNED_AT + timedelta(hours=1)
        view = surface_viewed_envelope(learner_id, "view-1", view_time)
        result = ingest_surface_viewed(application, evidence, view)
        assert (result.status, result.reason) == ("accepted", None)
        names = stored_event_names(application, learner_id)
        assert names.count("ExperimentAssigned") == 1
        assert names.count("ExperimentExposed") == 1
        live = exposure_state(application, learner_id)
        exposure = live["first_exposure"]
        assert exposure["variant"] == "variant_a"
        assert exposure["exposure_surface"] == TREATMENT_SURFACE
        assert exposure["assignment_ref"] == "experiment_assignment:exp_007"
        assert datetime.fromisoformat(exposure["exposed_at"]) >= datetime.fromisoformat(
            live["assignments"][EXP_ID]["assigned_at"]
        )
        assert datetime.fromisoformat(exposure["exposed_at"]) == view_time
        assert evidence.list(learner_id) == []

    # 5. A fresh application on the same files shows the same state.
    with open_persisted(tmp_path) as (application, evidence):
        assert exposure_state(application, learner_id) == live
        names = stored_event_names(application, learner_id)
        assert names.count("ExperimentAssigned") == 1
        assert names.count("ExperimentExposed") == 1
        assert evidence.list(learner_id) == []


def test_variant_b_learner_viewing_the_surface_is_not_exposed(tmp_path):
    with open_persisted(tmp_path) as (application, evidence):
        learner_id = create_learner(application, "variant_b")
        assert assign_learner_to_experiment(application, learner_id, EXP_ID, ASSIGNED_AT) is True

        view = surface_viewed_envelope(learner_id, "view-1", ASSIGNED_AT + timedelta(hours=1))
        result = ingest_surface_viewed(application, evidence, view)

        assert (result.status, result.reason) == ("rejected", "no_treatment_surface_for_variant")
        names = stored_event_names(application, learner_id)
        assert names.count("ExperimentAssigned") == 1
        assert names.count("ExperimentExposed") == 0
        assert exposure_state(application, learner_id)["first_exposure"] is None
        assert [(r.kind, r.reason) for r in evidence.list(learner_id)] == [
            ("rejected", "no_treatment_surface_for_variant")
        ]
