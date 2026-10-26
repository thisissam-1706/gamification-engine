"""Cohort gaming guardrail (metrics-framework 5.3): the weekly repeat-XP share.

"Decayed XP" is read as XP awarded on repeat completions, meaning awards whose
``repeat_index`` is >= 1 (the amount actually awarded, which under ruleset v2
is already decayed). An award with no repeat_index counts as a non-repeat. The
function is pure and only reports; it never changes assignment or any award.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
import re
from typing import Protocol

from src.abuse.rules import DEFAULT_ABUSE_RULES_VERSION, AbuseRules, get_abuse_rules

_WEEK_KEY = re.compile(r"^\d{4}-W\d{2}$")


class AwardLike(Protocol):
    """Anything with these fields, e.g. XPAwardFact or detectors.CompletionFact."""

    learner_id: str
    occurred_at: datetime
    xp_amount: int
    repeat_index: int | None


@dataclass(frozen=True)
class XPAwardFact:
    learner_id: str
    occurred_at: datetime
    xp_amount: int
    repeat_index: int | None


@dataclass(frozen=True)
class CohortGamingReport:
    cohort_id: str
    week: str
    total_xp: int
    repeat_xp: int
    share: float
    threshold: float
    breached: bool


def iso_week_key(occurred_at: datetime) -> str:
    """ISO calendar week ``YYYY-Www`` of a timestamp taken in UTC (naive means UTC)."""
    moment = (
        occurred_at.replace(tzinfo=timezone.utc)
        if occurred_at.tzinfo is None
        else occurred_at.astimezone(timezone.utc)
    )
    year, week, _ = moment.isocalendar()
    return f"{year}-W{week:02d}"


def cohort_gaming_report(
    awards: Iterable[AwardLike],
    cohort_of: Mapping[str, str],
    week: str,
    rules: AbuseRules | str = DEFAULT_ABUSE_RULES_VERSION,
) -> list[CohortGamingReport]:
    """Per cohort: total XP, repeat XP, share and ``breached = share > threshold`` for one week.

    Every cohort in ``cohort_of`` is reported (zero XP gives share 0 and is not
    breached); awards of unmapped learners are ignored. The comparison is exact.
    """
    if not _WEEK_KEY.match(week):
        raise ValueError(f"week must look like 'YYYY-Www', got {week!r}")
    threshold = get_abuse_rules(rules).cohort_gaming_threshold
    totals: dict[str, int] = defaultdict(int)
    repeats: dict[str, int] = defaultdict(int)
    for award in awards:
        cohort = cohort_of.get(award.learner_id)
        if cohort is None or iso_week_key(award.occurred_at) != week:
            continue
        totals[cohort] += award.xp_amount
        if award.repeat_index is not None and award.repeat_index >= 1:
            repeats[cohort] += award.xp_amount
    limit = Fraction(str(threshold))
    reports = []
    for cohort in sorted(set(cohort_of.values())):
        total, repeat = totals[cohort], repeats[cohort]
        reports.append(
            CohortGamingReport(
                cohort_id=cohort,
                week=week,
                total_xp=total,
                repeat_xp=repeat,
                share=repeat / total if total > 0 else 0.0,
                threshold=threshold,
                breached=total > 0 and Fraction(repeat, total) > limit,
            )
        )
    return reports
