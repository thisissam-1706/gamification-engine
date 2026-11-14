"""Streak-risk reminders for the exp_007 treatment arm.

``plan_streak_risk_reminders`` turns one learner's state into the reminders that are due now;
``run_streak_risk_sweep`` hands the latest due reminder of each learner to the real
``NotificationDispatcher``. Every instant is passed in (``now_utc``); nothing reads the clock.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Sequence
from uuid import UUID
from zoneinfo import ZoneInfo

from src.experiments.eligibility import (  # noqa: F401  (re-exported for existing importers)
    EXCLUDED_OPTED_OUT,
    EXCLUDED_TENURE,
    MIN_TENURE,
    MISSING_PREFERENCES,
    evaluate_eligibility,
)
from src.experiments.store import ExperimentStore
from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.guardrails import evaluate_quiet_hours
from src.notifications.models import (
    ChannelType,
    LearnerPreferences,
    NotificationCategory,
    NotificationProposal,
    TerminalFact,
)
from src.state_machine.learner import Learner, LearnerApplication
from src.state_machine.rules import calculate_streak

# Learner-local reminder times; the gaps halve (60, 30, 15 minutes). Change them here only.
REMINDER_TIMES_LOCAL = ("20:00", "21:00", "21:30", "21:45")

STREAK_RISK_TEMPLATE_ID = "streak_risk_v1"
UNASSIGNED = "unassigned"  # decision-row variant for a learner with no stored assignment
TREATMENT_VARIANT = "variant_a"  # exp_007 treatment; variant_b is control

# Skip reasons reported by the sweep.
NOT_TREATMENT = "not_treatment"
STREAK_ALREADY_BROKEN = "streak_already_broken"
ALREADY_ACTIVE_TODAY = "already_active_today"
NO_STREAK = "no_streak"
QUIET_HOURS = "quiet_hours"
NOTHING_DUE = "nothing_due"


@dataclass(frozen=True)
class SweepOutcome:
    """What the sweep did for one learner: a dispatcher fact, or the reason nothing was sent."""

    learner_id: str
    fact: TerminalFact | None = None
    reason: str | None = None


@dataclass(frozen=True)
class SweepResult:
    outcomes: list[SweepOutcome]


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _streak_can_continue_today(learner: Learner, local_date: str) -> bool:
    """True if a lesson today would extend the streak, by the rule ``calculate_streak`` applies
    (consecutive days, or a freeze bridging a missed day); False if it would restart at 1."""
    outcomes = calculate_streak(
        set(learner.activity_dates) | {local_date},
        learner.freeze_acquisitions.items(),
    )
    return outcomes[local_date][0] > 1


def _evaluate(
    learner: Learner,
    prefs: LearnerPreferences,
    now_utc: datetime,
    experiment_id: str,
) -> tuple[list[NotificationProposal], str | None]:
    """Returns (due proposals oldest first, skip reason). Exactly one of them is non-empty."""
    assignment = learner.experiment_assignments.get(experiment_id)
    if assignment is None or assignment["variant"] != TREATMENT_VARIANT:
        return [], NOT_TREATMENT
    eligible, reason = evaluate_eligibility(learner.created_on, prefs, now_utc)
    if not eligible:
        return [], reason

    tz = ZoneInfo(prefs.timezone or "UTC")
    local_date = now_utc.astimezone(tz).date()
    # Stored activity dates are compared as they are; they are not re-derived in prefs.timezone.
    active_today = local_date.isoformat() in learner.activity_dates
    if (
        learner.activity_dates
        and not active_today
        and not _streak_can_continue_today(learner, local_date.isoformat())
    ):
        return [], STREAK_ALREADY_BROKEN
    if active_today:
        return [], ALREADY_ACTIVE_TODAY
    streak = learner.new_streak
    if streak < 1:
        return [], NO_STREAK
    in_quiet, _ = evaluate_quiet_hours(now_utc, prefs.timezone or "UTC")
    if in_quiet:
        return [], QUIET_HOURS

    proposals: list[NotificationProposal] = []
    for hhmm in REMINDER_TIMES_LOCAL:
        hour, minute = (int(part) for part in hhmm.split(":"))
        scheduled = datetime(
            local_date.year, local_date.month, local_date.day, hour, minute, tzinfo=tz
        )
        if scheduled.astimezone(timezone.utc) > now_utc:
            continue
        proposals.append(
            NotificationProposal(
                proposal_id=f"streak_risk:{learner.id}:{local_date.isoformat()}:{hour:02d}{minute:02d}",
                learner_id=str(learner.id),
                channel=ChannelType.PUSH,
                category=NotificationCategory.STREAK_RISK,
                template_id=STREAK_RISK_TEMPLATE_ID,
                payload={"streak_count": streak, "local_date": local_date.isoformat()},
                created_at=_iso_utc(scheduled),
                correlation_id=f"streak_risk:{local_date.isoformat()}",
                experiment_id=experiment_id,
                variant=assignment["variant"],
            )
        )
    if not proposals:
        return [], NOTHING_DUE
    return proposals, None


def plan_streak_risk_reminders(
    learner: Learner,
    prefs: LearnerPreferences,
    now_utc: datetime,
    *,
    experiment_id: str = "exp_007",
) -> list[NotificationProposal]:
    """Every streak-risk reminder due for the learner's current local day, oldest first.

    Empty unless the learner is in the treatment variant, is not excluded (tenure of at least 3
    days, not opted out of push or streak_risk), has a streak that a lesson today could still
    extend, has no stored activity on the current local date, and ``now_utc`` is outside quiet
    hours.
    """
    proposals, _ = _evaluate(learner, prefs, now_utc, experiment_id)
    return proposals


def run_streak_risk_sweep(
    application: LearnerApplication,
    dispatcher: NotificationDispatcher,
    learner_ids: Sequence[str],
    prefs_by_learner: Mapping[str, LearnerPreferences],
    *,
    now_utc: datetime,
    experiment_id: str = "exp_007",
    store: ExperimentStore | None = None,
) -> SweepResult:
    """Dispatches each learner's latest due reminder; repeats are deduplicated by proposal id.

    With ``store``, a learner whose stored eligibility row is ineligible is skipped (reason
    ``ineligible:<stored reason>``) before any evaluation, and one ``experiment_decisions`` row
    is recorded per learner outcome (the first record for a given ``now_utc`` wins).
    """
    outcomes: list[SweepOutcome] = []

    def _decision(learner_id, variant, outcome, reason=None, proposal_id=None, fact=None):
        if store is None:
            return
        store.record_decision(
            {
                "exp_id": experiment_id,
                "learner_id": learner_id,
                "variant": variant,
                "decided_at": now_utc.isoformat(),
                "outcome": outcome,
                "reason": reason,
                "proposal_id": proposal_id,
                "fact_event_id": None if fact is None else fact.event_id,
                "fact_status": None if fact is None else fact.status.value,
            }
        )

    for learner_id in learner_ids:
        prefs = prefs_by_learner.get(learner_id)
        if prefs is None:
            outcomes.append(SweepOutcome(learner_id, reason=MISSING_PREFERENCES))
            if store is not None:
                learner = application.repository.get(UUID(learner_id))
                assignment = learner.experiment_assignments.get(experiment_id)
                variant = assignment["variant"] if assignment else UNASSIGNED
                _decision(learner_id, variant, "skipped", MISSING_PREFERENCES)
            continue
        learner = application.repository.get(UUID(learner_id))
        assignment = learner.experiment_assignments.get(experiment_id)
        variant = assignment["variant"] if assignment else UNASSIGNED
        if store is not None:
            stored = store.get_eligibility(experiment_id, learner_id)
            if stored is not None and not stored[0]:
                reason = f"ineligible:{stored[1]}"
                outcomes.append(SweepOutcome(learner_id, reason=reason))
                _decision(learner_id, variant, "skipped", reason)
                continue
        proposals, reason = _evaluate(learner, prefs, now_utc, experiment_id)
        if reason is not None:
            outcomes.append(SweepOutcome(learner_id, reason=reason))
            _decision(learner_id, variant, "skipped", reason)
            continue
        fact = dispatcher.dispatch(proposals[-1], prefs=prefs, now_utc=now_utc)
        outcomes.append(SweepOutcome(learner_id, fact=fact))
        _decision(learner_id, variant, "sent", None, proposals[-1].proposal_id, fact)
    return SweepResult(outcomes)
