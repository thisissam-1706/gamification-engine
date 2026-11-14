"""Keeps the leaderboard projection current from the learner event log.

The caller decides when to run it: there are no threads and no sleeping here. ``poll_once`` reads the
notification-log entries after the store's committed offset and applies each with
``projection.apply(n, offset=n.id)``; ``run_until_idle`` repeats until a poll has nothing left to do.

Why this is safe to run repeatedly, after a crash, or from a new process:
- the committed offset is the highest offset such that every offset up to it has been processed, so a
  poll resumes right after it;
- ``apply`` handles one entry in one store transaction, so an entry that fails leaves no trace: it is
  neither recorded nor committed, earlier entries stay applied, and the next poll retries it;
- an offset the store has already processed is a no-op in ``apply`` (it returns "duplicate").

The poller does not rehydrate the projection's in-memory cache. A new projection reads what it needs
from the store, so a restart only needs a new store and projection on the same database.
"""

from eventsourcing.application import Application

from src.leaderboard.projection import LeaderboardProjection

DEFAULT_BATCH_SIZE = 500


def poll_once(
    application: Application,
    projection: LeaderboardProjection,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Applies up to ``batch_size`` notification-log entries after the committed offset.

    Returns how many entries were newly processed (every kind counts, including ones the projection
    ignores, because they advance the checkpoint). Entries whose offset the store had already
    processed are skipped and not counted, so a poll that finds only those returns 0. The log is read
    in sections of at most ``notification_log.section_size``.

    An exception from ``projection.apply`` propagates; the entry that raised is not recorded, so the
    next call retries it.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    store = projection.store
    log = application.notification_log
    start = store.get_committed_offset("global") + 1
    read = 0
    processed = 0
    while read < batch_size:
        section = list(log.select(start=start, limit=min(log.section_size, batch_size - read)))
        if not section:
            break
        for notification in section:
            read += 1
            if store.is_offset_processed(notification.id):
                continue  # processed out of order earlier; the checkpoint passes it on its own
            projection.apply(notification, offset=notification.id)
            processed += 1
        start = section[-1].id + 1
    return processed


def run_until_idle(
    application: Application,
    projection: LeaderboardProjection,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Calls ``poll_once`` until it processes nothing; returns the total processed."""
    total = 0
    while True:
        processed = poll_once(application, projection, batch_size=batch_size)
        if processed == 0:
            return total
        total += processed
