"""Week 4 Fix 10: concurrent EvidenceStore openers migrate delivery_ids safely.

Two processes opening the same un-migrated database used to race: both read
PRAGMA table_info, both ran ALTER TABLE ADD COLUMN, and the slower one died
with "duplicate column name". The migration now runs in one BEGIN IMMEDIATE
transaction. Each test starts two spawn workers once and runs ROUNDS rounds;
in every round both workers open the same fresh file at the same moment.
"""

import multiprocessing
import sqlite3

import pytest

from src.ingestion.evidence import DELIVERY_COLUMNS, EvidenceStore
from src.state_machine.learner import Learner, LearnerApplication

ROUNDS = 20
WORKERS = 2
TIMEOUT_SECONDS = 60
EXPECTED_COLUMNS = ["event_id", "learner_id", "action_id", "digest", *DELIVERY_COLUMNS]


def _open_in_rounds(databases: list[str], barrier, results) -> None:
    """Top-level so spawn can import it; reports every outcome, never raises."""
    for database in databases:
        try:
            barrier.wait(timeout=TIMEOUT_SECONDS)
            evidence = EvidenceStore(database)
            evidence.close()
            results.put((database, "ok"))
        except Exception as exc:
            results.put((database, f"{type(exc).__name__}: {exc}"))


def _columns(database: str) -> list[str]:
    with sqlite3.connect(database) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(delivery_ids)")]
    connection.close()
    return columns


def _run_concurrent_openers(databases: list[str]) -> dict[str, list[str]]:
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(WORKERS)
    results = context.Queue()
    workers = [
        context.Process(target=_open_in_rounds, args=(databases, barrier, results))
        for _ in range(WORKERS)
    ]
    for worker in workers:
        worker.start()
    try:
        outcomes: dict[str, list[str]] = {database: [] for database in databases}
        for _ in range(WORKERS * len(databases)):
            try:
                database, outcome = results.get(timeout=TIMEOUT_SECONDS)
            except Exception:  # queue.Empty
                pytest.fail(
                    f"no worker result within {TIMEOUT_SECONDS}s; "
                    f"exit codes {[worker.exitcode for worker in workers]}"
                )
            outcomes[database].append(outcome)
        for worker in workers:
            worker.join(TIMEOUT_SECONDS)
            if worker.is_alive():
                pytest.fail(f"worker {worker.pid} did not exit within {TIMEOUT_SECONDS}s")
            assert worker.exitcode == 0
        return outcomes
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(5)


def test_two_processes_migrate_a_fresh_database_concurrently(tmp_path):
    """Parent creates only the event store, as the concurrent ingestion test does."""
    databases = []
    for index in range(ROUNDS):
        database = str(tmp_path / f"events-{index}.db")
        application = LearnerApplication(
            env={"PERSISTENCE_MODULE": "eventsourcing.sqlite", "SQLITE_DBNAME": database}
        )
        application.save(Learner())
        application.close()
        databases.append(database)

    outcomes = _run_concurrent_openers(databases)

    for database in databases:
        assert outcomes[database] == ["ok", "ok"], database
        assert _columns(database) == EXPECTED_COLUMNS


def test_two_processes_migrate_a_pre_provenance_database_concurrently(tmp_path):
    """An old four-column delivery_ids with rows: both open, rows kept with NULLs."""
    databases = []
    for index in range(ROUNDS):
        database = str(tmp_path / f"old-{index}.db")
        with sqlite3.connect(database) as connection:
            connection.executescript(
                """
                CREATE TABLE accepted_deliveries (
                    learner_id TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    PRIMARY KEY (learner_id, action_id)
                );
                CREATE TABLE delivery_ids (
                    event_id TEXT PRIMARY KEY,
                    learner_id TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    digest TEXT
                );
                INSERT INTO accepted_deliveries VALUES ('learner-1', 'cmp-A', 'event-A');
                INSERT INTO delivery_ids VALUES ('event-A', 'learner-1', 'cmp-A', 'digest-A');
                """
            )
        connection.close()
        databases.append(database)

    outcomes = _run_concurrent_openers(databases)

    for database in databases:
        assert outcomes[database] == ["ok", "ok"], database
        assert _columns(database) == EXPECTED_COLUMNS
        with sqlite3.connect(database) as connection:
            rows = connection.execute(
                f"SELECT event_id, learner_id, action_id, digest, {', '.join(DELIVERY_COLUMNS)} "
                "FROM delivery_ids"
            ).fetchall()
        connection.close()
        assert rows == [("event-A", "learner-1", "cmp-A", "digest-A", *[None] * len(DELIVERY_COLUMNS))]
