"""Deterministic fixed-probability experiment assignment."""

import hashlib


def assign(subject_id: str, exp_id: str) -> tuple[str, float]:
    """Assign a subject to the fixed 50/50 experiment variant."""
    digest = hashlib.sha256(f"{subject_id}{exp_id}".encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], byteorder="big") % 100
    variant = "variant_a" if bucket < 50 else "variant_b"
    return variant, 0.5
