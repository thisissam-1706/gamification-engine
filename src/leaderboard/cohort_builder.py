"""Weekly cohort builder: assigns learners to a cohort for an ISO-week epoch.

Inputs are only operational: the learner's accepted lesson completions in the ISO week before the
epoch (their count picks the activity tier; the module prefix of each ``lesson_id`` picks the
module). Nothing else about a learner is read.

Convention: ``lesson_id`` is ``"<module_id>/<lesson>"``. The module is the text before the FIRST
``"/"``; a lesson_id with no ``"/"`` (or an empty prefix) has no module.

Weekly rules enforced here:
- a learner who already has a cohort for the epoch keeps it (assignments are fixed for the week);
- a learner whose last lesson is 30 or more days before ``reference_time`` (``is_inactive``) is
  excluded and not assigned; a learner with no lesson history at all is new, not inactive;
- a cohort with more than ``max_size`` new members is split into equal parts;
- cohorts below ``min_size`` are merged with ``merge_undersized_cohorts``; a cohort that cannot be
  merged without exceeding ``max_size`` stays as it is, so ``max_size`` always holds.
"""

from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import re
from uuid import UUID

from src.leaderboard.cohorts import (
    CohortCapacityOverflowError,
    assign_activity_volume_cohort,
    assign_module_cohort,
    get_calendar_week,
    is_inactive,
    merge_undersized_cohorts,
)
from src.leaderboard.models import CohortDefinition, CohortMembership
from src.state_machine.learner import Learner, LearnerApplication

GROUPING_RULE_VERSION = "cohort_builder_v1"
# Hash buckets per tier and per module: the defaults of the two assign_* functions in cohorts.py.
DEFAULT_ACTIVITY_BUCKETS = 10
DEFAULT_MODULE_BUCKETS = 5
STRATEGY = "activity_volume"
DEFAULT_MODULE = "general"
_EPOCH_PATTERN = re.compile(r"^(\d{4})-W(\d{2})$")


@dataclass(frozen=True)
class CohortBuildResult(Mapping):
    """The outcome of one build.

    ``assignments`` (learner_id -> cohort_id) and ``cohort_counts`` cover every learner who has a
    cohort for the epoch after the build, including ones assigned earlier. ``definitions`` holds a
    nominal ``CohortDefinition`` per cohort in ``assignments`` (epoch, the build's min/max size, one
    strategy; status "forming", because the builder does not know a cohort's lifecycle).
    ``inactive`` lists the learners excluded as inactive, sorted.

    It also behaves as a read-only mapping of ``assignments`` (``result[learner_id]``,
    ``len(result)``, iteration), so callers can use it directly as learner_id -> cohort_id.
    """

    assignments: dict[str, str] = field(default_factory=dict)
    cohort_counts: dict[str, int] = field(default_factory=dict)
    definitions: dict[str, CohortDefinition] = field(default_factory=dict)
    inactive: tuple[str, ...] = ()

    def __getitem__(self, learner_id: str) -> str:
        return self.assignments[learner_id]

    def __iter__(self) -> Iterator[str]:
        return iter(self.assignments)

    def __len__(self) -> int:
        return len(self.assignments)


def prior_week(epoch: str) -> str:
    """The ISO week string before ``epoch`` (``"2026-W42"`` -> ``"2026-W41"``)."""
    match = _EPOCH_PATTERN.match(epoch)
    if match is None:
        raise ValueError(f"epoch must look like 'YYYY-Www', got {epoch!r}")
    monday = date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)
    year, week, _ = (monday - timedelta(days=7)).isocalendar()
    return f"{year}-W{week:02d}"


def module_of(lesson_id: str) -> str | None:
    """The text before the first "/" of ``lesson_id``; None when there is none."""
    prefix, separator, _ = lesson_id.partition("/")
    return prefix if separator and prefix else None


def _as_utc(value: str | datetime) -> datetime:
    parsed = (
        datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    )
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _scan_lessons(
    application: LearnerApplication, learner_id: str, week: str, reference_time: datetime
) -> tuple[list[str], datetime | None]:
    """(lesson_ids completed in ``week`` (UTC), latest completion time at or before reference_time)."""
    lessons: list[str] = []
    last_active: datetime | None = None
    for domain_event in application.events.get(UUID(learner_id)):
        if not isinstance(domain_event, Learner.LessonCompleted):
            continue
        if domain_event.occurred_at is None:
            continue
        occurred = _as_utc(domain_event.occurred_at)
        if get_calendar_week(occurred) == week:
            lessons.append(domain_event.lesson_id)
        if occurred <= reference_time and (last_active is None or occurred > last_active):
            last_active = occurred
    return lessons, last_active


def choose_module(lesson_ids: Sequence[str]) -> str:
    """Most-completed module; ties go to the lexicographically smallest; else "general"."""
    counts = Counter(m for m in map(module_of, lesson_ids) if m is not None)
    if not counts:
        return DEFAULT_MODULE
    best = max(counts.values())
    return min(module for module, count in counts.items() if count == best)


def _split_oversized(
    members_by_cohort: dict[str, list[str]], max_size: int
) -> dict[str, list[str]]:
    """Splits any cohort above ``max_size`` into equal parts (sorted by learner id).

    The first part keeps the cohort id; later parts get ``__p<n>``.
    """
    result: dict[str, list[str]] = {}
    for cohort_id in sorted(members_by_cohort):
        members = sorted(members_by_cohort[cohort_id])
        parts = -(-len(members) // max_size)  # ceil
        base, extra = divmod(len(members), parts)
        position = 0
        for index in range(parts):
            size = base + (1 if index < extra else 0)
            part_id = cohort_id if index == 0 else f"{cohort_id}__p{index}"
            result[part_id] = members[position : position + size]
            position += size
    return result


def _merge_undersized(
    members_by_cohort: dict[str, list[str]],
    last_active: dict[str, str],
    epoch: str,
    min_size: int,
    max_size: int,
    reference_time: datetime,
    assigned_at: str,
) -> dict[str, str]:
    """Merges cohorts below ``min_size`` with ``merge_undersized_cohorts``; learner -> cohort id.

    If the whole merge would exceed ``max_size`` the cohorts are tried one at a time, and a cohort
    that fits nowhere is left as it is.
    """
    definitions = {
        cid: CohortDefinition(cid, STRATEGY, epoch, min_size, max_size, "forming")  # type: ignore[arg-type]
        for cid in sorted(members_by_cohort)
    }
    memberships = {
        cid: [
            CohortMembership(learner, cid, epoch, assigned_at, "active", last_active[learner])
            for learner in members
        ]
        for cid, members in members_by_cohort.items()
    }

    def settled(merged: dict[str, list[CohortMembership]]) -> dict[str, str]:
        return {m.subject_id: m.cohort_id for ms in merged.values() for m in ms}

    try:
        _, merged = merge_undersized_cohorts(
            list(definitions.values()), memberships, min_size, reference_time
        )
        return settled(merged)
    except CohortCapacityOverflowError:
        pass

    working = {cid: list(ms) for cid, ms in memberships.items()}
    undersized = [cid for cid in definitions if len(working[cid]) < min_size]
    viable = [cid for cid in definitions if cid not in undersized]
    for cid in undersized:
        try:
            _, merged = merge_undersized_cohorts(
                [definitions[cid]] + [definitions[v] for v in viable],
                {c: working[c] for c in [cid] + viable},
                min_size,
                reference_time,
            )
        except CohortCapacityOverflowError:
            continue  # fits nowhere: stays as it is
        working.update(merged)
    return settled(working)


def build_weekly_cohorts(
    application: LearnerApplication,
    learner_ids: Sequence[str],
    epoch: str,
    *,
    min_size: int = 500,
    max_size: int = 5000,
    reference_time: datetime,
    activity_buckets: int = DEFAULT_ACTIVITY_BUCKETS,
    module_buckets: int = DEFAULT_MODULE_BUCKETS,
) -> CohortBuildResult:
    """Assigns every eligible learner to this epoch's cohort and records it on the learner.

    The base cohort id is ``<activity cohort id>__<module cohort id>``, built from
    ``assign_activity_volume_cohort`` (prior-week lesson count) and ``assign_module_cohort``
    (chosen module). New members are then split (``max_size``) and merged (``min_size``) as
    described in the module docstring, and each is stored with ``Learner.assign_cohort`` using
    ``grouping_rule_version="cohort_builder_v1"``, ``assigned_at=reference_time`` and
    correlation id ``cohort_assignment:<epoch>``.

    A learner who already has a cohort for ``epoch`` keeps it and is reported with it; running
    the builder again therefore changes nothing. Learners are processed in sorted order and
    duplicates in ``learner_ids`` are ignored. Splitting and merging consider only learners
    newly assigned in this call: members assigned by an earlier call are neither moved nor
    counted toward ``min_size`` or ``max_size``.

    ``activity_buckets`` and ``module_buckets`` are the hash bucket counts passed to
    ``assign_activity_volume_cohort`` and ``assign_module_cohort`` (``num_cohorts``). The defaults
    suit populations of thousands. For a tiny population use 1, so a cohort is exactly one tier
    and module; with the defaults almost every learner would sit alone in a bucket and the merge
    step would collapse the whole population into one cohort.
    """
    if activity_buckets < 1 or module_buckets < 1:
        raise ValueError("bucket counts must be at least 1")
    if reference_time.tzinfo is None or reference_time.utcoffset() is None:
        raise ValueError("reference_time must be timezone-aware")
    if min_size > max_size:
        raise ValueError("min_size must not exceed max_size")
    if max_size < 1:
        raise ValueError("max_size must be at least 1")
    reference_time = reference_time.astimezone(timezone.utc)
    reference_iso = reference_time.isoformat()
    week = prior_week(epoch)

    fixed: dict[str, str] = {}
    inactive: list[str] = []
    pending: dict[str, list[str]] = {}
    last_active_iso: dict[str, str] = {}
    for learner_id in sorted(set(learner_ids)):
        stored = application.repository.get(UUID(learner_id)).cohort_memberships.get(epoch)
        if stored is not None:
            fixed[learner_id] = str(stored["cohort_id"])
            continue
        lessons, last_active = _scan_lessons(application, learner_id, week, reference_time)
        if last_active is not None and is_inactive(last_active, reference_time):
            inactive.append(learner_id)
            continue
        module = choose_module(lessons)
        base_cohort = (
            f"{assign_activity_volume_cohort(learner_id, epoch, len(lessons), num_cohorts=activity_buckets)}"
            f"__{assign_module_cohort(learner_id, epoch, module, num_cohorts=module_buckets)}"
        )
        pending.setdefault(base_cohort, []).append(learner_id)
        # A learner with no history is new: treated as active as of the reference time.
        last_active_iso[learner_id] = (last_active or reference_time).isoformat()

    assignments: dict[str, str] = dict(fixed)
    if pending:
        placed = _merge_undersized(
            _split_oversized(pending, max_size),
            last_active_iso,
            epoch,
            min_size,
            max_size,
            reference_time,
            reference_iso,
        )
        for learner_id in sorted(placed):
            learner = application.repository.get(UUID(learner_id))
            if learner.assign_cohort(
                placed[learner_id],
                epoch,
                grouping_rule_version=GROUPING_RULE_VERSION,
                assigned_at=reference_time,
                correlation_id=f"cohort_assignment:{epoch}",
            ):
                application.save(learner)
                assignments[learner_id] = placed[learner_id]
            else:
                assignments[learner_id] = str(learner.cohort_memberships[epoch]["cohort_id"])

    counts = Counter(assignments.values())
    ordered = sorted(counts)
    return CohortBuildResult(
        assignments=dict(sorted(assignments.items())),
        cohort_counts={cohort: counts[cohort] for cohort in ordered},
        definitions={
            cohort: CohortDefinition(cohort, STRATEGY, epoch, min_size, max_size, "forming")  # type: ignore[arg-type]
            for cohort in ordered
        },
        inactive=tuple(inactive),
    )
