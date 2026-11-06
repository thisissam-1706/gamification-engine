"""Exposure rules: which surfaces count as the treatment for each experiment variant."""

EXPOSURE_SURFACES: dict[str, dict[str, frozenset[str]]] = {
    "exp_007": {"variant_a": frozenset({"streak_risk_reminder"})},
}


class ExposureRefused(ValueError):
    """An exposure that the rules do not allow; ``str(exc)`` is the reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
