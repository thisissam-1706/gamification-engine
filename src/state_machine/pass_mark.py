"""Pass mark for graded lessons.

A lesson completion may carry its score (``questions_correct`` of ``questions_total``). When more
than half of the answers are wrong, the completion earns no XP: the engine stores a single
XPAwarded fact of 0 XP with ``withheld_reason = "below_pass_mark"`` and nothing else. The attempt
does not count toward the streak or quests and does not use up a decay step, so passing the same
lesson afterwards earns what a first pass would. Exactly half wrong still passes. A completion
without a score (every event stored before this rule, and producers that do not grade) passes.
"""

PASS_MARK_REASON = "below_pass_mark"


def below_pass_mark(questions_correct: int | None, questions_total: int | None) -> bool:
    """True when more than 50% of the answers are wrong; False when no score was given."""
    if questions_correct is None or questions_total is None:
        return False
    wrong = questions_total - questions_correct
    return 2 * wrong > questions_total
