"""Experiment runner: composes the stored events, eligibility, guardrails and metrics into one run.

Everything is read from stored data and the arguments; nothing reads a clock, the network or a
random source, and nothing is written.
"""

from collections.abc import Collection, Iterable
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta, timezone

from src.abuse.guardrail import AwardLike, CohortGamingReport, cohort_gaming_report, iso_week_key
from src.abuse.service import AbuseDetectionService
from src.experiments.inputs import build_mastery_inputs, read_assignments
from src.experiments.metrics import MasteryReport, mastery_report
from src.experiments.srm import srm_check
from src.experiments.store import ExperimentStore
from src.notifications.store import NotificationStore
from src.scale_and_recovery.rebuild import read_all_notifications
from src.state_machine.learner import Learner, LearnerApplication

TREATMENT_VARIANT = "variant_a"
CONTROL_VARIANT = "variant_b"
CONTROL_NOTE = (
    "no exposure rate: the exposure rules give variant_b no treatment surface, "
    "so a control learner cannot be exposed"
)


@dataclass(frozen=True)
class ArmExposure:
    eligible: int
    exposed: int
    rate: float | None
    note: str | None = None


@dataclass(frozen=True)
class ExposureByArm:
    arms: dict[str, ArmExposure]


def _instant(value: str | datetime) -> datetime:
    return value if isinstance(value, datetime) else datetime.fromisoformat(value)


def exposure_by_arm(
    application: LearnerApplication,
    exp_id: str,
    *,
    eligible_ids: Collection[str],
    as_of: datetime,
) -> ExposureByArm:
    """Exposure among ELIGIBLE learners per arm, from stored events; nothing is written.

    A learner is exposed when they have at least one ExperimentExposed for ``exp_id`` with
    ``exposed_at <= as_of``. Treatment rate = exposed / eligible (None when nobody is eligible).
    The control rate is always None: variant_b has no exposure surface (see ``CONTROL_NOTE``).
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    keep = set(eligible_ids)
    variants: dict[str, str] = {}
    exposed: set[str] = set()
    for notification in read_all_notifications(application):
        event = application.mapper.to_domain_event(notification)
        learner_id = str(event.originator_id)
        if learner_id not in keep:
            continue
        if isinstance(event, Learner.ExperimentAssigned):
            if event.exp_id == exp_id:
                variants.setdefault(learner_id, event.variant)
        elif isinstance(event, Learner.ExperimentExposed):
            if event.exp_id == exp_id and _instant(event.exposed_at) <= as_of:
                exposed.add(learner_id)

    treatment = {i for i, v in variants.items() if v == TREATMENT_VARIANT}
    control = {i for i, v in variants.items() if v == CONTROL_VARIANT}
    treated_exposed = len(exposed & treatment)
    return ExposureByArm(
        {
            TREATMENT_VARIANT: ArmExposure(
                eligible=len(treatment),
                exposed=treated_exposed,
                rate=treated_exposed / len(treatment) if treatment else None,
            ),
            CONTROL_VARIANT: ArmExposure(
                eligible=len(control), exposed=0, rate=None, note=CONTROL_NOTE
            ),
        }
    )


# --- the full run ---------------------------------------------------------------------------

CAP_BREACH_TREATMENT = "cap_breach_treatment"
GAMING_SHARE_BREACH = "gaming_share_breach"
SRM_ASSIGNED = "srm_assigned"
SRM_ELIGIBLE = "srm_eligible"
MISSING_ELIGIBILITY_RECORDS = "missing_eligibility_records"
NO_ELIGIBLE_LEARNERS = "no_eligible_learners"

ARM_VARIANTS = {"treatment": TREATMENT_VARIANT, "control": CONTROL_VARIANT}


@dataclass(frozen=True)
class ArmResult:
    eligible: int
    mastery: MasteryReport
    exposure: ArmExposure
    breach_learner_days: int
    breach_learners: int
    breach_learner_rate: float | None  # distinct breached eligible learners / eligible


@dataclass(frozen=True)
class GamingResult:
    breached: bool
    weeks: dict[str, list[CohortGamingReport]]


def _plain(value):
    """JSON-plain copy: dataclasses and mappings become dicts with sorted keys; tuples become lists."""
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if isinstance(value, dict):
        return {str(key): _plain(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


@dataclass(frozen=True)
class ExperimentRunResult:
    exp_id: str
    as_of: datetime
    valid: bool
    invalid_reasons: list[str]
    arms: dict[str, ArmResult]
    srm_assigned: dict
    srm_eligible: dict
    gaming: GamingResult

    def to_dict(self) -> dict:
        """Plain JSON types, keys sorted at every level; reports come from ``dataclasses.asdict``."""
        return _plain(self)


def awards_from_abuse_service(service: AbuseDetectionService) -> list[AwardLike]:
    """The accepted-completion facts (with their XP awards) of the abuse service, as a list."""
    return list(service.completion_facts()[0])


def _utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _weeks(first: datetime, as_of: datetime) -> list[str]:
    """Every ISO week key (UTC) from ``first`` through ``as_of``."""
    last = iso_week_key(as_of)
    weeks: list[str] = []
    cursor = first
    while iso_week_key(cursor) <= last:
        key = iso_week_key(cursor)
        if key not in weeks:
            weeks.append(key)
        cursor += timedelta(days=7)
    if last not in weeks and iso_week_key(first) <= last:
        weeks.append(last)
    return weeks


def run_experiment(
    application: LearnerApplication,
    store: ExperimentStore,
    notification_store: NotificationStore,
    awards: Iterable[AwardLike],
    *,
    exp_id: str = "exp_007",
    assessment_id: str,
    as_of: datetime,
) -> ExperimentRunResult:
    """Reports the experiment's primary metric, exposure and guardrails; never raises on bad data.

    Eligibility is the persisted ``experiment_eligibility`` rows, not recomputed. An invalid run is
    still fully reported: ``valid`` is False and ``invalid_reasons`` says why. ``as_of`` is required
    (None or naive raises ValueError).
    """
    if as_of is None:
        raise ValueError("as_of is required: declare the data cutoff")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")

    assignments, variants = read_assignments(application, exp_id)
    stored = {row["learner_id"]: row for row in store.list_eligibility(exp_id)}
    eligible_ids = {i for i in assignments if i in stored and stored[i]["eligible"]}
    arm_of = {name: {i for i in eligible_ids if variants[i] == v} for name, v in ARM_VARIANTS.items()}

    inputs = build_mastery_inputs(
        application, exp_id, assessment_id=assessment_id, eligible_ids=eligible_ids
    )
    exposure = exposure_by_arm(application, exp_id, eligible_ids=eligible_ids, as_of=as_of)

    breaches: dict[str, set[tuple[str, object]]] = {name: set() for name in ARM_VARIANTS}
    for row in notification_store.list_guardrail_violations(experiment_id=exp_id):
        learner_id = row["learner_id"]
        occurred = _utc(row["occurred_at"])
        if occurred > as_of:
            continue
        for name, members in arm_of.items():
            if learner_id in members:
                breaches[name].add((learner_id, occurred.date()))

    arms: dict[str, ArmResult] = {}
    for name, variant in ARM_VARIANTS.items():
        members = arm_of[name]
        arm_assignments = {i: t for i, t in inputs.assignments.items() if i in members}
        report = mastery_report(
            arm_assignments,
            [a for a in inputs.attempts if a["learner_id"] in members],
            [c for c in inputs.completions if c["learner_id"] in members],
            as_of=as_of,
        )
        breached_learners = {learner for learner, _ in breaches[name]}
        arms[name] = ArmResult(
            eligible=len(members),
            mastery=report,
            exposure=exposure.arms[variant],
            breach_learner_days=len(breaches[name]),
            breach_learners=len(breached_learners),
            breach_learner_rate=len(breached_learners) / len(members) if members else None,
        )

    counts = {v: sum(1 for x in variants.values() if x == v) for v in ARM_VARIANTS.values()}
    srm_assigned = srm_check(counts[TREATMENT_VARIANT], counts[CONTROL_VARIANT])
    srm_eligible = srm_check(len(arm_of["treatment"]), len(arm_of["control"]))

    cohort_of = {i: variants[i] for i in eligible_ids}
    in_scope = [a for a in awards if _utc(a.occurred_at) <= as_of]
    weeks: dict[str, list[CohortGamingReport]] = {}
    if assignments:
        first = min(_utc(t) for t in assignments.values())
        for week in _weeks(first, as_of):
            weeks[week] = cohort_gaming_report(in_scope, cohort_of, week)
    gaming = GamingResult(
        breached=any(r.breached for reports in weeks.values() for r in reports), weeks=weeks
    )

    reasons: list[str] = []
    if arms["treatment"].breach_learner_days:
        reasons.append(CAP_BREACH_TREATMENT)
    if gaming.breached:
        reasons.append(GAMING_SHARE_BREACH)
    if srm_assigned["alert"]:
        reasons.append(SRM_ASSIGNED)
    if srm_eligible["alert"]:
        reasons.append(SRM_ELIGIBLE)
    if any(i not in stored for i in assignments):
        reasons.append(MISSING_ELIGIBILITY_RECORDS)
    if not eligible_ids:
        reasons.append(NO_ELIGIBLE_LEARNERS)
    return ExperimentRunResult(
        exp_id=exp_id,
        as_of=as_of,
        valid=not reasons,
        invalid_reasons=reasons,
        arms=arms,
        srm_assigned=srm_assigned,
        srm_eligible=srm_eligible,
        gaming=gaming,
    )
