"""Pure helpers for the exp_007 fatigue metrics: observation window, rolling 24 h maximum, Wald interval.

No clock, no I/O: every instant is passed in.
"""

import math
from collections.abc import Iterable
from datetime import datetime, timedelta

from src.experiments.metrics import WINDOW

ROLLING = timedelta(hours=24)
SECONDS_PER_DAY = 86_400
WALD_Z = 1.96


def observation_window(assigned_at: datetime, as_of: datetime) -> tuple[datetime, datetime] | None:
    """[assigned_at, min(assigned_at + 14 days, as_of)): start inclusive, end exclusive.

    None when the window is empty (the learner was assigned at or after ``as_of``).
    """
    end = min(assigned_at + WINDOW, as_of)
    return (assigned_at, end) if end > assigned_at else None


def window_days(window: tuple[datetime, datetime] | None) -> float:
    """Length of a window in days (fractional); 0.0 for no window."""
    if window is None:
        return 0.0
    return (window[1] - window[0]).total_seconds() / SECONDS_PER_DAY


def in_window(instants: Iterable[datetime], window: tuple[datetime, datetime] | None) -> list[datetime]:
    """The instants with start <= t < end, sorted."""
    if window is None:
        return []
    return sorted(t for t in instants if window[0] <= t < window[1])


def max_in_rolling_24h(instants: Iterable[datetime]) -> int:
    """Largest count in any window [t, t + 24 h) that starts at one of the instants (0 for none)."""
    times = sorted(instants)
    best = 0
    for i, start in enumerate(times):
        end = start + ROLLING
        count = 0
        for t in times[i:]:
            if t >= end:
                break
            count += 1
        best = max(best, count)
    return best


def wald_interval(p1: float, n1: int, p0: float, n0: int) -> dict[str, float] | None:
    """95% Wald interval of p1 - p0 from the two rates and denominators; None if a denominator is 0."""
    if n1 <= 0 or n0 <= 0:
        return None
    difference = p1 - p0
    se = math.sqrt(p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
    return {"low": difference - WALD_Z * se, "high": difference + WALD_Z * se}
