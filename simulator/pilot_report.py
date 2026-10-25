"""Intention-to-treat report over a simulated exp_007 pilot run."""

import math
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from simulator.pilot import CAP, DATA_CUTOFF_ISO, EXP_ID, PilotRun
from src.experiments.metrics import mastery_report

ARM_VARIANTS = {"treatment": "variant_a", "control": "variant_b"}
SENT = ("delivered", "provider_failed")
SRM_ALPHA = 0.001


def _ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def max_in_rolling_24h(timestamps: list[str]) -> int:
    """Largest count in any window [t, t + 24h) that starts at a timestamp."""
    times = sorted(_ts(t) for t in timestamps)
    best = 0
    for i, start in enumerate(times):
        end = start + timedelta(hours=24)
        count = 0
        for t in times[i:]:
            if t >= end:
                break
            count += 1
        best = max(best, count)
    return best


def srm_check(assigned_variant_a: int, assigned_variant_b: int) -> dict:
    """Chi-square sample-ratio-mismatch check against a 50/50 split."""
    total = assigned_variant_a + assigned_variant_b
    expected = total / 2
    chi2 = (
        sum((o - expected) ** 2 / expected for o in (assigned_variant_a, assigned_variant_b))
        if total
        else 0.0
    )
    p_value = math.erfc(math.sqrt(chi2 / 2))
    return {
        "assigned_variant_a": assigned_variant_a,
        "assigned_variant_b": assigned_variant_b,
        "chi2": chi2,
        "p_value": p_value,
        "test": "chi_square_1df",
        "alpha": SRM_ALPHA,
        "expected_split": "50/50",
        "alert": p_value < SRM_ALPHA,
    }


def _buckets(mastery, assigned: dict, eligible_ids: set, attempts: list) -> dict:
    """Partition eligible learners by how their outcome is treated in the headline.

    A learner is mature iff assigned_at + 14 days <= DATA_CUTOFF_ISO. Attempts after
    the cutoff are not observable, so they do not count as an attempt record.
    """
    cutoff = _ts(DATA_CUTOFF_ISO)
    mature = {l for l in eligible_ids if _ts(assigned[l]) + timedelta(days=14) <= cutoff}
    attempted = {a["learner_id"] for a in attempts if _ts(a["occurred_at"]) <= cutoff}
    missing_no_attempt = len(mature - attempted)
    with_outcome = mastery.mastered + mastery.failed
    invalid = mastery.invalid_ordering + mastery.conflicting
    return {
        "mature_with_outcome": with_outcome,
        "mature_no_outcome": mastery.missing_outcome,
        "missing_no_attempt": missing_no_attempt,
        "missing_attempt_no_completion": mastery.missing_outcome - missing_no_attempt,
        "mature_invalid": invalid,
        "immature": mastery.immature,
        "headline_denominator": with_outcome + mastery.missing_outcome + invalid,
        "headline_rate": mastery.rate,
    }


def _funnel(run: PilotRun, arm_ids: set, eligible_ids: set, assigned: dict,
            mastery, attempts: list, deliveries: list) -> dict:
    """Learner counts from assignment to mastery; the same keys for both arms."""
    cutoff = _ts(DATA_CUTOFF_ISO)
    reasons = [e["reason"] for e in run.eligibility if e["learner_id"] in arm_ids and not e["eligible"]]
    decided = {d["learner_id"]: d["action"] for d in run.decisions if d["learner_id"] in eligible_ids}
    sent = {d["learner_id"] for d in deliveries if d["status"] in SENT}
    delivered = {d["learner_id"] for d in deliveries if d["status"] == "delivered"}
    exposure_rows = [x for x in run.exposures if x["learner_id"] in eligible_ids]
    exposed_before = sum(1 for x in exposure_rows if x["before_attempt"])
    first_attempt = {}
    for a in attempts:
        at = _ts(a["occurred_at"])
        if at <= cutoff and (a["learner_id"] not in first_attempt or at < first_attempt[a["learner_id"]]):
            first_attempt[a["learner_id"]] = at
    mature = {l for l in eligible_ids if _ts(assigned[l]) + timedelta(days=14) <= cutoff}
    attempted = mature & set(first_attempt)
    completed = {c["learner_id"] for c in run.completions
                 if c["learner_id"] in attempted and _ts(c["occurred_at"]) <= cutoff}
    return {
        "assigned": len(arm_ids),
        "excluded_tenure": reasons.count("tenure_lt_3_days"),
        "excluded_consent": reasons.count("push_streak_risk_opt_out"),
        "eligible": len(eligible_ids),
        "decided_send_reminders": sum(1 for a in decided.values() if a == "send_reminders"),
        "decided_no_action": sum(1 for a in decided.values() if a == "no_action"),
        "any_message_sent": len(sent & eligible_ids),
        "any_message_delivered": len(delivered & eligible_ids),
        "exposed": len(exposure_rows),
        "exposed_before_attempt": exposed_before,
        "exposed_after_attempt": len(exposure_rows) - exposed_before,
        "mature": len(mature),
        "immature": len(eligible_ids) - len(mature),
        "attempted": len(attempted),
        "completion_logged": len(completed),
        "mastered": mastery.mastered,
    }


def _arm_report(run: PilotRun, variant: str) -> dict:
    assigned = {a["learner_id"]: a["assigned_at"] for a in run.assignments if a["variant"] == variant}
    eligible_ids = {
        e["learner_id"] for e in run.eligibility if e["eligible"] and e["learner_id"] in assigned
    }
    arm_ids = set(assigned)

    arm_attempts = [a for a in run.attempts if a["learner_id"] in eligible_ids]
    mastery = mastery_report(
        {l: assigned[l] for l in eligible_ids},
        arm_attempts,
        [c for c in run.completions if c["learner_id"] in eligible_ids],
        as_of=DATA_CUTOFF_ISO,
    )

    exposed = sum(1 for x in run.exposures if x["learner_id"] in eligible_ids)
    deliveries = [d for d in run.deliveries if d["learner_id"] in arm_ids]
    status_counts = {s: 0 for s in (*SENT, "suppressed_cap", "suppressed_consent")}
    sent_times = defaultdict(list)
    for d in deliveries:
        status_counts[d["status"]] += 1
        if d["status"] in SENT:
            sent_times[d["learner_id"]].append(d["scheduled_at"])
    per_learner_max = [max_in_rolling_24h(times) for times in sent_times.values()]

    eligible = len(eligible_ids)
    return {
        "assigned": len(arm_ids),
        "excluded": len(arm_ids) - eligible,
        "eligible": eligible,
        "exposed": exposed,
        "exposure_rate": exposed / eligible if eligible else None,
        "mastery": asdict(mastery),
        "funnel": _funnel(run, arm_ids, eligible_ids, assigned, mastery, arm_attempts, deliveries),
        "buckets": _buckets(mastery, assigned, eligible_ids, arm_attempts),
        "missing_outcome_rate": (
            mastery.missing_outcome / mastery.denominator if mastery.denominator else None
        ),
        "messages_sent": status_counts["delivered"] + status_counts["provider_failed"],
        "delivered": status_counts["delivered"],
        "provider_failed": status_counts["provider_failed"],
        "suppressed_cap": status_counts["suppressed_cap"],
        "suppressed_consent": status_counts["suppressed_consent"],
        "opt_outs": sum(1 for d in deliveries if d["opted_out_after"]),
        "max_sent_in_24h": max(per_learner_max, default=0),
        "cap_breach_learners": sum(1 for m in per_learner_max if m > CAP),
    }


def _contrast(treatment: dict, control: dict) -> dict:
    p1, p0 = treatment["mastery"]["rate"], control["mastery"]["rate"]
    if p1 is None or p0 is None:
        return dict.fromkeys(
            ("treatment_rate", "control_rate", "difference", "standard_error",
             "ci95_low", "ci95_high", "conclusive")
        )
    n1, n0 = treatment["mastery"]["denominator"], control["mastery"]["denominator"]
    difference = p1 - p0
    se = math.sqrt(p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
    low, high = difference - 1.96 * se, difference + 1.96 * se
    return {
        "treatment_rate": p1,
        "control_rate": p0,
        "difference": difference,
        "standard_error": se,
        "ci95_low": low,
        "ci95_high": high,
        "conclusive": low > 0 or high < 0,
    }


def analyze(run: PilotRun) -> dict:
    """Build the ITT report by assigned arm among eligible learners. Does not mutate run."""
    arms = {name: _arm_report(run, variant) for name, variant in ARM_VARIANTS.items()}
    counts = {v: sum(1 for a in run.assignments if a["variant"] == v) for v in ARM_VARIANTS.values()}
    return {
        "meta": {
            "setting": run.setting_name,
            "seed": run.seed,
            "population_size": run.population_size,
            "exp_id": EXP_ID,
            "data_cutoff": DATA_CUTOFF_ISO,
            "notes": [
                "primary analysis is intention-to-treat; exposure is reported separately",
                "headline mastery rate = mastered / (mature learners with an outcome + mature "
                "learners with no outcome, counted as not mastered + mature invalid); immature "
                "learners (assigned_at + 14 days > data cutoff) are excluded and reported separately",
                "synthetic data; results do not demonstrate real-world causality",
            ],
        },
        "arms": arms,
        "contrast": _contrast(arms["treatment"], arms["control"]),
        "srm": srm_check(counts["variant_a"], counts["variant_b"]),
        "srm_eligible": srm_check(arms["treatment"]["eligible"], arms["control"]["eligible"]),
    }


LEDGER_BUCKETS = (
    "excluded", "immature", "mastered", "failed", "missing_no_attempt",
    "missing_no_completion", "invalid_ordering", "conflicting",
)


def _classify(learner_id: str, assigned_at: str, attempts: list, completions: list) -> str:
    """Bucket one eligible learner by running mastery_report on that learner alone."""
    report = mastery_report(
        {learner_id: assigned_at}, attempts, completions, as_of=DATA_CUTOFF_ISO
    )
    for bucket in ("immature", "mastered", "failed", "invalid_ordering", "conflicting"):
        if getattr(report, bucket) == 1:
            return bucket
    assert report.missing_outcome == 1
    return "missing_no_completion" if attempts else "missing_no_attempt"


def build_ledger(run: PilotRun) -> list[dict]:
    """One row per assigned learner, sorted by learner_id, tracing them through the funnel."""
    eligibility = {e["learner_id"]: e for e in run.eligibility}
    actions = {d["learner_id"]: d["action"] for d in run.decisions}
    exposures = {x["learner_id"]: x for x in run.exposures}
    cutoff_iso = DATA_CUTOFF_ISO
    deliveries, attempts, completions = defaultdict(list), defaultdict(list), defaultdict(list)
    for d in run.deliveries:
        deliveries[d["learner_id"]].append(d)
    # Only records observable at the data cutoff are shown.
    for a in run.attempts:
        if a["occurred_at"] <= cutoff_iso:
            attempts[a["learner_id"]].append(a)
    for c in run.completions:
        if c["occurred_at"] <= cutoff_iso:
            completions[c["learner_id"]].append(c)

    rows = []
    for assignment in sorted(run.assignments, key=lambda a: a["learner_id"]):
        learner_id = assignment["learner_id"]
        eligible = eligibility[learner_id]["eligible"]
        statuses = [d["status"] for d in deliveries[learner_id]]
        attempt_rows = sorted(attempts[learner_id], key=lambda a: a["occurred_at"])
        attempt_at = attempt_rows[0]["occurred_at"] if attempt_rows else None
        completion_rows = completions[learner_id]
        exposure = exposures.get(learner_id)
        exposed_at = exposure["exposed_at"] if exposure else None
        if eligible:
            bucket = _classify(
                learner_id, assignment["assigned_at"], attempt_rows, completion_rows
            )
        else:
            bucket = "excluded"
        rows.append({
            "learner_id": learner_id,
            "variant": assignment["variant"],
            "assigned_at": assignment["assigned_at"],
            "eligible": eligible,
            "exclusion_reason": None if eligible else eligibility[learner_id]["reason"],
            "action": actions[learner_id],
            "messages_sent": sum(1 for s in statuses if s in SENT),
            "messages_delivered": statuses.count("delivered"),
            "messages_provider_failed": statuses.count("provider_failed"),
            "messages_suppressed_cap": statuses.count("suppressed_cap"),
            "messages_suppressed_consent": statuses.count("suppressed_consent"),
            "exposed": exposed_at is not None,
            "exposed_at": exposed_at,
            "exposure_before_attempt": exposure["before_attempt"] if exposure else None,
            "attempted": attempt_at is not None,
            "attempt_at": attempt_at,
            "completed": bool(completion_rows),
            "passed": completion_rows[0]["passed"] if completion_rows else None,
            "outcome_bucket": bucket,
        })
    return rows
