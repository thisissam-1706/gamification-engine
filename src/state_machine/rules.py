"""Small deterministic reward configuration used at the decision boundary."""

XP_AMOUNTS = {"v1": 100}


def xp_amount_for(rule_version: str) -> int:
    """Returns the currently configured amount for a rule version."""
    return XP_AMOUNTS[rule_version]
