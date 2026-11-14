"""Weekly cohort builder: assigns learners to a cohort for an ISO-week epoch.

Inputs are only operational: the learner's accepted lesson completions in the ISO week before the
epoch (their count picks the activity tier; the module prefix of each ``lesson_id`` picks the
module). Nothing else about a learner is read.

Convention: ``lesson_id`` is ``"<module_id>/<lesson>"``. The module is the text before the FIRST
``"/"``; a lesson_id with no ``"/"`` (or an empty prefix) has no module.

Weekly rules enforced here:
- a learner who already has a cohort for the epoch keeps it (assignments are fixed for the week and
  never moved), and those members count toward ``min_size`` and ``max_size`` of their cohort when
  new learners arrive in a later call;
- a learner whose last lesson is 30 or more days before ``reference_time`` (``is_inactive``) is
  excluded and not assigned; a learner with no lesson history at all is new, not inactive;
- hash buckets: left automatic (``activity_buckets`` and ``module_buckets`` not passed), the builder
  uses 1 activity bucket and ``max(1, group_size // (2 * min_size))`` module buckets per tier-and-module
  group, so production defaults (min 500, max 5000) give cohorts of about twice ``min_size``; explicit
  counts are used as given;
- late learners (a later call for the same epoch), automatic buckets only: each tier-and-module group
  of new learners first joins, whole, an existing cohort of the same tier that has room (members
  counted; same module first, then lowest cohort id), so bucket counts never move a late learner.
  Groups that fit no existing cohort are bucketed: the new members of a base cohort id that already
  has members join it if the whole group fits (existing size + new members <= ``max_size``);
  otherwise the group goes to new part cohorts (``__p<n>``) split equally to respect ``max_size``;
  a base id with no members yet is split the same way (the first part keeps the base id);
- a cohort below ``min_size`` is merged (with ``merge_undersized_cohorts``) into its nearest cohort
  that has room, counting existing members in every size: same activity tier first, then same
  module, then lowest cohort id; a cohort that fits nowhere stays as it is, so ``max_size`` always
  holds. Only cohorts made entirely of new learners are merged away; a cohort with existing members
  is never moved, though new groups can merge into it.
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
# Automatic hash buckets (when the caller does not pass bucket counts): one activity bucket, and per
# tier-and-module group max(1, group_size // (AUTO_BUCKET_MIN_MULTIPLE * min_size)) module buckets, so
# a bucket averages about twice ``min_size`` learners.
AUTO_ACTIVITY_BUCKETS = 1
AUTO_BUCKET_MIN_MULTIPLE = 2
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


def _tier_name(lesson_count: int, epoch: str) -> str | None:
    """The activity tier for a prior-week lesson count, read from the id ``cohorts.py`` builds."""
    probe = assign_activity_volume_cohort("tier-probe", epoch, lesson_count, num_cohorts=1)
    return _tier_and_module(probe, epoch)[0]


def choose_module(lesson_ids: Sequence[str]) -> str:
    """Most-completed module; ties go to the lexicographically smallest; else "general"."""
    counts = Counter(m for m in map(module_of, lesson_ids) if m is not None)
    if not counts:
        return DEFAULT_MODULE
    best = max(counts.values())
    return min(module for module, count in counts.items() if count == best)


def _place_new(
    pending: dict[str, list[str]],
    existing: dict[str, list[str]],
    max_size: int,
) -> dict[str, list[str]]:
    """Groups the new learners into cohorts that respect ``max_size``: cohort id -> new learners.

    ``existing`` maps cohort id -> members assigned earlier. A base cohort id with existing members
    takes its new group whole if it fits. Otherwise the group is split equally into new part cohorts
    (``__p<n>``, skipping ids already used); the first part keeps the base id when no one holds it.
    """
    taken = set(existing)
    placed: dict[str, list[str]] = {}
    for base in sorted(pending):
        members = sorted(pending[base])
        if base in existing and len(existing[base]) + len(members) <= max_size:
            placed[base] = members
            continue
        parts = -(-len(members) // max_size)  # ceil
        size, extra = divmod(len(members), parts)
        position, suffix = 0, 0
        for index in range(parts):
            if index == 0 and base not in taken:
                part_id = base
            else:
                suffix += 1
                while f"{base}__p{suffix}" in taken:
                    suffix += 1
                part_id = f"{base}__p{suffix}"
            taken.add(part_id)
            count = size + (1 if index < extra else 0)
            placed[part_id] = members[position : position + count]
            position += count
    return placed


def _tier_and_module(cohort_id: str, epoch: str) -> tuple[str | None, str | None]:
    """The activity tier and module of a cohort id built by this module (None if it does not parse)."""
    activity, _, rest = cohort_id.partition("__")
    module_part = rest.partition("__")[0]
    activity_prefix, module_prefix = f"cohort_act_{epoch}_", f"cohort_mod_{epoch}_"
    tier = (
        activity[len(activity_prefix):].rsplit("_", 1)[0] if activity.startswith(activity_prefix) else None
    )
    module = (
        module_part[len(module_prefix):].rsplit("_", 1)[0]
        if module_part.startswith(module_prefix)
        else None
    )
    return tier, module


def _merge_undersized(
    members_by_cohort: dict[str, list[str]],
    existing: dict[str, list[str]],
    last_active: dict[str, str],
    epoch: str,
    min_size: int,
    max_size: int,
    reference_time: datetime,
    assigned_at: str,
) -> dict[str, str]:
    """Merges each new cohort below ``min_size`` into its nearest cohort; learner -> cohort id.

    ``members_by_cohort`` holds the NEW learners per cohort id and ``existing`` the members assigned
    earlier. Sizes always include both. Only cohorts made entirely of new learners are merged away
    (existing members are never moved), one at a time, smallest first (then by cohort id). The
    target is chosen among the OTHER cohorts that have room (size after the merge <= ``max_size``),
    existing ones included, ranked by: same activity tier first, then same module, then lowest cohort
    id. The members move with ``merge_undersized_cohorts`` called on the pair with the target first,
    so the target keeps its id. A cohort that fits nowhere stays as it is, so ``max_size`` always
    holds. A merged cohort that is still below ``min_size`` is considered again. The result covers
    only the new learners.
    """
    cohort_ids = sorted(set(existing) | set(members_by_cohort))
    definitions = {
        cid: CohortDefinition(cid, STRATEGY, epoch, min_size, max_size, "forming")  # type: ignore[arg-type]
        for cid in cohort_ids
    }
    working: dict[str, list[CohortMembership]] = {
        cid: [
            # Earlier members have no activity scan here: they are active as of the reference time.
            CohortMembership(learner, cid, epoch, assigned_at, "active", assigned_at)
            for learner in existing.get(cid, ())
        ]
        + [
            CohortMembership(learner, cid, epoch, assigned_at, "active", last_active[learner])
            for learner in members_by_cohort.get(cid, ())
        ]
        for cid in cohort_ids
    }
    new_learners = {learner for members in members_by_cohort.values() for learner in members}
    stuck: set[str] = set()
    while True:
        waiting = sorted(
            (
                cid
                for cid in working
                if cid not in existing and len(working[cid]) < min_size and cid not in stuck
            ),
            key=lambda cid: (len(working[cid]), cid),
        )
        if not waiting:
            break
        source = waiting[0]
        source_tier, source_module = _tier_and_module(source, epoch)

        def rank(target: str) -> tuple[bool, bool, str]:
            tier, module = _tier_and_module(target, epoch)
            return (tier != source_tier, module != source_module, target)

        targets = sorted(
            (c for c in working if c != source and len(working[c]) + len(working[source]) <= max_size),
            key=rank,
        )
        for target in targets:
            try:
                _, merged = merge_undersized_cohorts(
                    [definitions[target], definitions[source]],
                    {target: working[target], source: working[source]},
                    min_size,
                    reference_time,
                )
            except CohortCapacityOverflowError:
                continue  # this target does not work out: try the next nearest
            working[target] = merged[target]
            del working[source]
            break
        else:
            stuck.add(source)  # fits nowhere: stays as it is
    return {
        m.subject_id: m.cohort_id
        for ms in working.values()
        for m in ms
        if m.subject_id in new_learners
    }


def build_weekly_cohorts(
    application: LearnerApplication,
    learner_ids: Sequence[str],
    epoch: str,
    *,
    min_size: int = 500,
    max_size: int = 5000,
    reference_time: datetime,
    activity_buckets: int | None = None,
    module_buckets: int | None = None,
) -> CohortBuildResult:
    """Assigns every eligible learner to this epoch's cohort and records it on the learner.

    The base cohort id is ``<activity cohort id>__<module cohort id>``, built from
    ``assign_activity_volume_cohort`` (prior-week lesson count) and ``assign_module_cohort``
    (chosen module). New learners are then placed, split (``max_size``) and merged (``min_size``)
    as described in the module docstring, and each is stored with ``Learner.assign_cohort`` using
    ``grouping_rule_version="cohort_builder_v1"``, ``assigned_at=reference_time`` and
    correlation id ``cohort_assignment:<epoch>``.

    A learner who already has a cohort for ``epoch`` keeps it and is reported with it; running
    the builder again with no new learners therefore changes nothing. Such earlier members count
    toward ``min_size`` and ``max_size`` of their cohort, so a late learner can join it (if there is
    room) or be merged into it, and no cohort is pushed past ``max_size``. Pass the whole
    population on every call: the builder can only count the members it is given, so earlier members
    left out of ``learner_ids`` are not counted. Learners are processed in sorted order and
    duplicates in ``learner_ids`` are ignored.

    ``activity_buckets`` and ``module_buckets`` are the hash bucket counts passed to
    ``assign_activity_volume_cohort`` and ``assign_module_cohort`` (``num_cohorts``). Explicit
    numbers are used as given (a synthetic run passes 1 and 1, so a cohort is exactly one tier and
    module). When not passed (``None``) the builder sizes them itself: ``activity_buckets`` is 1 and,
    for each tier-and-module group, ``module_buckets`` is ``max(1, group_size // (2 * min_size))``, so
    a bucket averages about twice ``min_size`` learners and the production defaults (500 and 5000)
    give sensible cohorts. Group sizes are known once every learner has been scanned.

    With both left automatic, late learners are matched to an existing cohort first, so bucket counts
    never decide where a late learner goes unless no existing cohort can take it: each tier-and-module
    group of new learners joins, whole, an existing cohort of the same tier that has room (current
    members counted), ranked same module first then lowest cohort id. Only the groups that fit no
    existing cohort go through automatic bucket sizing, split and merge. Explicit bucket counts keep
    the previous behaviour (no matching step).
    """
    for name, value in (("activity_buckets", activity_buckets), ("module_buckets", module_buckets)):
        if value is not None and value < 1:
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
    existing: dict[str, list[str]] = {}
    inactive: list[str] = []
    scanned: dict[str, tuple[int, str]] = {}  # new learner -> (prior-week lesson count, module)
    last_active_iso: dict[str, str] = {}
    for learner_id in sorted(set(learner_ids)):
        stored = application.repository.get(UUID(learner_id)).cohort_memberships.get(epoch)
        if stored is not None:
            fixed[learner_id] = str(stored["cohort_id"])
            existing.setdefault(fixed[learner_id], []).append(learner_id)
            continue
        lessons, last_active = _scan_lessons(application, learner_id, week, reference_time)
        if last_active is not None and is_inactive(last_active, reference_time):
            inactive.append(learner_id)
            continue
        scanned[learner_id] = (len(lessons), choose_module(lessons))
        # A learner with no history is new: treated as active as of the reference time.
        last_active_iso[learner_id] = (last_active or reference_time).isoformat()

    # Late learners (automatic bucket sizing only): join an existing cohort of the same tier first.
    matched: dict[str, str] = {}
    if activity_buckets is None and module_buckets is None and existing:
        groups: dict[tuple[str | None, str], list[str]] = {}
        for learner_id, (count, module) in scanned.items():
            groups.setdefault((_tier_name(count, epoch), module), []).append(learner_id)
        for (tier, module), members in sorted(groups.items(), key=lambda item: (str(item[0][0]), item[0][1])):
            candidates = sorted(
                (
                    cohort_id
                    for cohort_id in existing
                    if _tier_and_module(cohort_id, epoch)[0] == tier
                    and len(existing[cohort_id]) + len(members) <= max_size
                ),
                key=lambda cohort_id: (_tier_and_module(cohort_id, epoch)[1] != module, cohort_id),
            )
            if not candidates:
                continue
            target = candidates[0]
            for learner_id in sorted(members):
                matched[learner_id] = target
                existing[target].append(learner_id)  # counted from now on, and never moved
                del scanned[learner_id]

    # Everything else is bucketed. Group sizes are known now that every learner has been scanned.
    group_size = Counter((_tier_name(count, epoch), module) for count, module in scanned.values())
    pending: dict[str, list[str]] = {}
    for learner_id, (count, module) in scanned.items():
        activity = AUTO_ACTIVITY_BUCKETS if activity_buckets is None else activity_buckets
        modules = (
            max(1, group_size[(_tier_name(count, epoch), module)] // (AUTO_BUCKET_MIN_MULTIPLE * min_size))
            if module_buckets is None
            else module_buckets
        )
        base_cohort = (
            f"{assign_activity_volume_cohort(learner_id, epoch, count, num_cohorts=activity)}"
            f"__{assign_module_cohort(learner_id, epoch, module, num_cohorts=modules)}"
        )
        pending.setdefault(base_cohort, []).append(learner_id)

    placed = dict(matched)
    if pending:
        placed.update(
            _merge_undersized(
                _place_new(pending, existing, max_size),
                existing,
                last_active_iso,
                epoch,
                min_size,
                max_size,
                reference_time,
                reference_iso,
            )
        )
    assignments: dict[str, str] = dict(fixed)
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
