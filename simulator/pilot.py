"""Pilot simulation of exp_007 event streams.

Limits of this pilot model:
- Exposure only affects an attempt if it precedes the learner's would-be attempt
  time, which is drawn independently of exposure.
- Eligibility, decision and delivery are simulator stand-ins for the M4/M5
  slice, not calls into those modules.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.experiments.assignment import assign

EXP_ID = "exp_007"
START = datetime(2026, 9, 1, tzinfo=timezone.utc)
DATA_CUTOFF_ISO = "2026-09-29T00:00:00Z"
ASSIGN_SPREAD_DAYS = 18
REMINDER_DAYS = 3
MAX_MESSAGES = 21
CAP = 5  # messages per rolling 24h
DELIVER_PROB = 0.9
EXPOSE_PROB = 0.7
COMPLETION_LOGGED_PROB = 0.95
MIN_TENURE_DAYS = 3
OPT_OUT_CONSENT_PROB = 0.10
ASSESSMENT_ID = "ckpt_1"
ASSESSMENT_VERSION = 1

SETTINGS = {
    "null": dict(effect=0.0, optout_rate=0.01, messages_per_day=1, enforce_cap=True),
    "positive": dict(effect=0.05, optout_rate=0.01, messages_per_day=1, enforce_cap=True),
    "harmful": dict(effect=-0.05, optout_rate=0.05, messages_per_day=1, enforce_cap=True),
    "cap_stress": dict(effect=0.0, optout_rate=0.05, messages_per_day=7, enforce_cap=False),
}


@dataclass(frozen=True)
class PilotRun:
    assignments: list
    eligibility: list
    decisions: list
    deliveries: list
    exposures: list
    attempts: list
    completions: list
    setting_name: str
    seed: int
    population_size: int


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def simulate(population: list, draws: dict, setting, seed: int) -> PilotRun:
    if isinstance(setting, str):
        setting_name, params = setting, SETTINGS[setting]
    else:
        setting_name, params = "custom", setting
    effect = params["effect"]
    optout_rate = params["optout_rate"]
    per_day = params["messages_per_day"]
    enforce_cap = params["enforce_cap"]

    assignments, eligibility, decisions = [], [], []
    deliveries, exposures, attempts, completions = [], [], [], []

    for persona in sorted(population, key=lambda p: p.learner_id):
        learner_id = persona.learner_id
        d = draws[learner_id]

        variant, propensity = assign(learner_id, EXP_ID)
        assigned_at = START + timedelta(days=int(d["assign"] * ASSIGN_SPREAD_DAYS))
        assignments.append({
            "learner_id": learner_id, "exp_id": EXP_ID, "variant": variant,
            "propensity": propensity, "assigned_at": _iso(assigned_at),
        })

        tenure_days = int(d["tenure"] * 31)
        if tenure_days < MIN_TENURE_DAYS:
            eligible, reason = False, "tenure_lt_3_days"
        elif d["consent"] < OPT_OUT_CONSENT_PROB:
            eligible, reason = False, "push_streak_risk_opt_out"
        else:
            eligible, reason = True, None
        eligibility.append({"learner_id": learner_id, "eligible": eligible, "reason": reason})

        # Decision never reads outcome draws.
        if eligible and variant == "variant_a":
            decision = {"action": "send_reminders", "policy": "streak_risk_reminder_v1",
                        "reason": "treatment"}
        elif eligible:
            decision = {"action": "no_action", "policy": None, "reason": "control"}
        else:
            decision = {"action": "no_action", "policy": None, "reason": "ineligible"}
        decisions.append({"learner_id": learner_id, **decision})

        first_delivered = None
        if decision["action"] == "send_reminders":
            opted_out = False
            counted = []  # scheduled times of delivered / provider_failed messages
            for k in range(per_day * REMINDER_DAYS):
                day, j = divmod(k, per_day)
                day += 1  # reminders go out on days 1..3 after assignment
                scheduled = assigned_at + timedelta(days=day, hours=9 + j)
                opted_out_after = False
                if opted_out:
                    status = "suppressed_consent"
                elif enforce_cap and sum(
                    1 for s in counted if scheduled - timedelta(hours=24) < s < scheduled
                ) >= CAP:
                    status = "suppressed_cap"
                else:
                    counted.append(scheduled)
                    if d["deliver"][k] < DELIVER_PROB:
                        status = "delivered"
                        if first_delivered is None:
                            first_delivered = scheduled
                        if d["optout"][k] < optout_rate:
                            opted_out = True
                            opted_out_after = True
                    else:
                        status = "provider_failed"
                deliveries.append({
                    "learner_id": learner_id, "message_id": f"M-{learner_id}-{k}",
                    "scheduled_at": _iso(scheduled), "status": status,
                    "opted_out_after": opted_out_after,
                })

        # Would-be attempt time: independent of exposure, computed for every learner.
        attempt_at = assigned_at + timedelta(
            days=1 + int(d["attempt_day"] * 13), hours=int(d["attempt_hour"] * 12)
        )
        exposed = first_delivered is not None and d["expose"] < EXPOSE_PROB
        before_attempt = exposed and first_delivered <= attempt_at
        if exposed:
            exposures.append({
                "learner_id": learner_id, "exposed_at": _iso(first_delivered),
                "before_attempt": before_attempt,
            })

        flag = 1 if before_attempt else 0
        p_attempt = _clamp(1 - persona.dropout_probability + effect * flag, 0.0, 1.0)
        if d["attempt"] < p_attempt:
            attempt_id = f"A-{learner_id}"
            attempts.append({
                "learner_id": learner_id, "attempt_id": attempt_id,
                "assessment_id": ASSESSMENT_ID, "assessment_version": ASSESSMENT_VERSION,
                "occurred_at": _iso(attempt_at),
            })
            passed = d["passed"] < _clamp(persona.base_pass_probability + effect * flag, 0.0, 1.0)
            if d["completion_logged"] < COMPLETION_LOGGED_PROB:
                lag = timedelta(minutes=10 + int(d["completion_lag"] * 50))
                completions.append({
                    "learner_id": learner_id, "attempt_id": attempt_id,
                    "completion_id": f"C-{learner_id}",
                    "assessment_id": ASSESSMENT_ID, "assessment_version": ASSESSMENT_VERSION,
                    "passed": bool(passed), "occurred_at": _iso(attempt_at + lag),
                })

    return PilotRun(
        assignments=assignments, eligibility=eligibility, decisions=decisions,
        deliveries=deliveries, exposures=exposures, attempts=attempts,
        completions=completions, setting_name=setting_name, seed=seed,
        population_size=len(population),
    )
