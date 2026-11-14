"""Experiment outcome reproduction from raw log stream (M7, M8)."""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import random
from typing import Any

from src.experiments.assignment import assign
from src.experiments.inputs import build_mastery_inputs, read_assignments
from src.experiments.metrics import MasteryReport, mastery_report
from src.experiments.runner import ExperimentRunResult, run_experiment
from src.experiments.store import ExperimentStore
from src.notifications.store import NotificationStore
from src.state_machine.learner import LearnerApplication


@dataclass
class ExperimentReproductionResult:
    """Quantitative results of reproducing experiment metrics from raw event streams."""

    population_size: int
    total_attempts: int
    total_completions: int
    as_of: str
    mastery_report_first: dict[str, Any]
    mastery_report_second: dict[str, Any]
    bit_identical: bool
    checksum_first: str
    checksum_second: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def generate_experiment_raw_logs(
    learner_ids: list[str],
    exp_id: str = "exp_007",
    seed: int = 42,
    base_time_iso: str = "2026-10-01T00:00:00Z",
) -> tuple[dict[str, str], list[dict[str, Any]], list[dict[str, Any]], str]:
    """Deterministically generates raw experiment events for a learner population."""
    rng = random.Random(seed)
    base_dt = datetime.fromisoformat(base_time_iso.replace("Z", "+00:00")).astimezone(timezone.utc)

    assignments: dict[str, str] = {}
    attempts: list[dict[str, Any]] = []
    completions: list[dict[str, Any]] = []

    for i, lid in enumerate(learner_ids):
        # 1. Deterministic assignment
        variant, propensity = assign(lid, exp_id)
        assign_dt = base_dt + timedelta(hours=i * 2)
        assign_iso = assign_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        assignments[lid] = assign_iso

        # 2. Synthetic attempt within 14-day window for ~80% of learners
        if rng.random() < 0.80:
            attempt_dt = assign_dt + timedelta(days=1 + (i % 5), hours=rng.randint(1, 10))
            attempt_id = f"att_{lid}_01"
            attempts.append(
                {
                    "attempt_id": attempt_id,
                    "learner_id": lid,
                    "assessment_id": "checkpoint_math_v1",
                    "assessment_version": 1,
                    "occurred_at": attempt_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            )

            # 3. Completion for ~90% of attempts
            if rng.random() < 0.90:
                comp_dt = attempt_dt + timedelta(minutes=15)
                passed = rng.random() > 0.35  # ~65% pass rate
                completions.append(
                    {
                        "attempt_id": attempt_id,
                        "learner_id": lid,
                        "assessment_id": "checkpoint_math_v1",
                        "assessment_version": 1,
                        "passed": passed,
                        "occurred_at": comp_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }
                )

    as_of = (base_dt + timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return assignments, attempts, completions, as_of


def verify_experiment_reproduction(
    learner_ids: list[str],
    exp_id: str = "exp_007",
    seed: int = 42,
) -> ExperimentReproductionResult:
    """Recomputes outcome metrics from raw logs twice and asserts bit-identical outputs."""
    assignments, attempts, completions, as_of = generate_experiment_raw_logs(
        learner_ids, exp_id=exp_id, seed=seed
    )

    # First computation from raw stream
    rep1 = mastery_report(assignments, attempts, completions, as_of=as_of)
    rep1_dict = asdict(rep1)
    rep1_json = json.dumps(rep1_dict, sort_keys=True)
    hash1 = hashlib.sha256(rep1_json.encode("utf-8")).hexdigest()

    # Second independent computation (shuffling input lists to verify order-independence)
    shuffled_attempts = list(attempts)
    shuffled_completions = list(completions)
    random.Random(seed + 99).shuffle(shuffled_attempts)
    random.Random(seed + 99).shuffle(shuffled_completions)

    rep2 = mastery_report(assignments, shuffled_attempts, shuffled_completions, as_of=as_of)
    rep2_dict = asdict(rep2)
    rep2_json = json.dumps(rep2_dict, sort_keys=True)
    hash2 = hashlib.sha256(rep2_json.encode("utf-8")).hexdigest()

    bit_identical = (hash1 == hash2)

    return ExperimentReproductionResult(
        population_size=len(learner_ids),
        total_attempts=len(attempts),
        total_completions=len(completions),
        as_of=as_of,
        mastery_report_first=rep1_dict,
        mastery_report_second=rep2_dict,
        bit_identical=bit_identical,
        checksum_first=hash1,
        checksum_second=hash2,
    )


@dataclass
class EngineReproductionResult:
    """Two independent runs of the whole experiment engine over one persisted log."""

    bit_identical: bool
    checksum_first: str
    checksum_second: str
    run_first: ExperimentRunResult
    run_second: ExperimentRunResult
    population_size: int  # learners assigned for exp_id in the log
    total_attempts: int  # checkpoint attempts for assessment_id by those learners

    def to_dict(self) -> dict[str, Any]:
        return {
            "bit_identical": self.bit_identical,
            "checksum_first": self.checksum_first,
            "checksum_second": self.checksum_second,
            "run_first": self.run_first.to_dict(),
            "run_second": self.run_second.to_dict(),
            "population_size": self.population_size,
            "total_attempts": self.total_attempts,
        }


_SQLITE_HEADER = b"SQLite format 3\x00"


def _checksum(run: ExperimentRunResult) -> str:
    """sha256 of the run's ``to_dict()`` as sorted-key JSON."""
    return hashlib.sha256(json.dumps(run.to_dict(), sort_keys=True).encode("utf-8")).hexdigest()


def _engine_pass(
    db_path: str, exp_id: str, assessment_id: str, as_of: datetime
) -> tuple[ExperimentRunResult, int, int]:
    """One run on FRESH application and store objects opened on ``db_path``; all closed afterwards."""
    application = LearnerApplication(
        env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": db_path}
    )
    try:
        store = ExperimentStore(db_path)
        try:
            notification_store = NotificationStore(db_path)
            try:
                run = run_experiment(
                    application,
                    store,
                    notification_store,
                    [],
                    exp_id=exp_id,
                    assessment_id=assessment_id,
                    as_of=as_of,
                )
                population = len(read_assignments(application, exp_id)[0])
                attempts = len(
                    build_mastery_inputs(application, exp_id, assessment_id=assessment_id).attempts
                )
                return run, population, attempts
            finally:
                notification_store.close()
        finally:
            store.close()
    finally:
        application.close()


def verify_engine_reproduction(
    db_path: str,
    *,
    exp_id: str,
    assessment_id: str,
    as_of: datetime,
) -> EngineReproductionResult:
    """Runs the experiment engine twice on fresh objects over one persisted log and compares checksums.

    ``db_path`` is one sqlite file holding the event store, the experiment tables and the
    notification tables. No awards are passed, so the gaming guardrail is not exercised. ``as_of``
    must be timezone-aware (``run_experiment`` raises ValueError otherwise). A ``db_path`` that is
    not an existing file raises FileNotFoundError before anything is opened or created. A non-empty file that is not
    a SQLite database raises ValueError (naming the path) before anything is opened; the file is not touched.
    """
    if not Path(db_path).is_file():
        raise FileNotFoundError(f"engine log does not exist: {db_path}")
    with open(db_path, "rb") as handle:
        header = handle.read(len(_SQLITE_HEADER))
    if header and header != _SQLITE_HEADER:
        raise ValueError(f"engine log is not a SQLite database: {db_path}")
    run_first, population, attempts = _engine_pass(db_path, exp_id, assessment_id, as_of)
    run_second, _, _ = _engine_pass(db_path, exp_id, assessment_id, as_of)
    first, second = _checksum(run_first), _checksum(run_second)
    return EngineReproductionResult(
        bit_identical=first == second,
        checksum_first=first,
        checksum_second=second,
        run_first=run_first,
        run_second=run_second,
        population_size=population,
        total_attempts=attempts,
    )
