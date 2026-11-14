"""Sample-ratio-mismatch check shared by the pilot report and the experiment runner."""

import math

SRM_ALPHA = 0.001


def srm_check(assigned_variant_a: int, assigned_variant_b: int) -> dict:
    """Chi-square sample-ratio-mismatch check against a 50/50 split."""
    total = assigned_variant_a + assigned_variant_b
    expected = total / 2
    chi2 = (
        sum((o - expected) ** 2 / expected for o in (assigned_variant_a, assigned_variant_b))
        if total
        else 0.0
    )
    p_value = math.erfc(math.sqrt(chi2 / 2))
    return {
        "assigned_variant_a": assigned_variant_a,
        "assigned_variant_b": assigned_variant_b,
        "chi2": chi2,
        "p_value": p_value,
        "test": "chi_square_1df",
        "alpha": SRM_ALPHA,
        "expected_split": "50/50",
        "alert": p_value < SRM_ALPHA,
    }
