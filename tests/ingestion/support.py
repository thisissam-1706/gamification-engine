"""Shared test helper: ingestion with the late-event window switched off.

Tests written before Week 4 Fix 11a use fixed occurred_at values in 2026-09,
which the default 72-hour window would now reject against the real clock.
They are not about lateness, so they ingest with late_window=None (unbounded)
and keep every other setting, including an explicitly passed rule version.
Late-window behaviour itself is tested in tests/ingestion/test_late_window.py.
"""

from dataclasses import replace

from src.ingestion.config import DEFAULT_CONFIG, IngestConfig
from src.ingestion.service import IngestionResult
from src.ingestion.service import ingest_lesson_completed as _ingest_lesson_completed

UNBOUNDED_CONFIG = replace(DEFAULT_CONFIG, late_window=None)


def ingest_lesson_completed(
    application, evidence, envelope, *, config: IngestConfig | None = None, **kwargs
) -> IngestionResult:
    """Drop-in for the service function, with late_window=None on any config."""
    config = UNBOUNDED_CONFIG if config is None else replace(config, late_window=None)
    return _ingest_lesson_completed(application, evidence, envelope, config=config, **kwargs)
