"""The weekly cycle in one call: finalize last week's cohorts, build this week's, update the leaderboard.

``run_weekly_cycle`` is for the caller (or a cron job) to run once per week; nothing here schedules
itself. Steps, in order:

0. catch up: ``run_until_idle`` so the leaderboard knows every cohort assignment already in the log
   (finalizing a cohort refuses later arrivals, so this keeps a late projection of last week's
   assignments from being refused);
1. finalize every cohort of the PREVIOUS epoch that is not yet finalized, using the lifecycle rules
   (``CohortLifecycleService``); a cohort whose status does not allow it is reported, not forced;
2. build this epoch's cohorts with ``build_weekly_cohorts``;
3. ``run_until_idle`` again, so this epoch's assignments and any XP are in the leaderboard.

This epoch's cohorts are NOT sealed here (a product decision). Running the cycle again for the same
epoch changes nothing: the catch-up and the last poll find nothing new, every previous cohort is
already finalized, and the builder keeps existing assignments.

The finalized status lives in the leaderboard store, not in the event log, so a leaderboard rebuilt
from the log starts with every cohort un-finalized; running the cycle again finalizes last week's
cohorts again.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from eventsourcing.application import Application

from src.leaderboard.cohort_builder import CohortBuildResult, build_weekly_cohorts, prior_week
from src.leaderboard.poller import run_until_idle
from src.leaderboard.projection import LeaderboardProjection


@dataclass(frozen=True)
class WeeklyCycleResult:
    """What one weekly cycle did.

    ``finalized`` are the previous epoch's cohorts finalized by this run, ``already_finalized`` the ones
    that already were, and ``not_finalized`` pairs ``(cohort_id, status)`` for cohorts the lifecycle
    rules would not finalize from that status. ``build`` is the cohort builder's result for this epoch.
    ``caught_up`` and ``applied`` count the log entries applied before finalizing and after building.
    """

    epoch: str
    previous_epoch: str
    caught_up: int
    finalized: tuple[str, ...]
    already_finalized: tuple[str, ...]
    not_finalized: tuple[tuple[str, str], ...]
    build: CohortBuildResult
    applied: int


def run_weekly_cycle(
    application: Application,
    projection: LeaderboardProjection,
    learner_ids: Sequence[str],
    epoch: str,
    *,
    reference_time: datetime,
    min_size: int = 500,
    max_size: int = 5000,
) -> WeeklyCycleResult:
    """Runs the weekly steps for ``epoch`` in order; see the module docstring.

    ``learner_ids`` must be the whole population every time (the builder counts only the members it is
    given). The previous epoch's cohorts are found through the leaderboard's cohort rows for that epoch.
    """
    previous = prior_week(epoch)
    caught_up = run_until_idle(application, projection)

    store = projection.store
    finalized: list[str] = []
    already: list[str] = []
    refused: list[tuple[str, str]] = []
    for cohort_id in sorted({entry.scope_id for entry in store.get_epoch_entries("cohort", previous)}):
        definition = store.get_cohort_definition(cohort_id)
        status = definition.status if definition is not None else "forming"
        if status == "finalized":
            already.append(cohort_id)
            continue
        try:
            projection.transition_cohort(cohort_id, "finalized", epoch=previous)
        except ValueError:
            refused.append((cohort_id, status))  # the lifecycle rules do not allow it: reported, not forced
        else:
            finalized.append(cohort_id)

    build = build_weekly_cohorts(
        application,
        learner_ids,
        epoch,
        min_size=min_size,
        max_size=max_size,
        reference_time=reference_time,
    )
    applied = run_until_idle(application, projection)
    return WeeklyCycleResult(
        epoch=epoch,
        previous_epoch=previous,
        caught_up=caught_up,
        finalized=tuple(finalized),
        already_finalized=tuple(already),
        not_finalized=tuple(refused),
        build=build,
        applied=applied,
    )
