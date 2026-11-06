"""Deterministic fixed-probability experiment assignment."""

import hashlib
import json

# Identifies the hashing scheme in assign(); stored on every ExperimentAssigned event.
HASH_VERSION = "hash_v2"


def assign(subject_id: str, exp_id: str) -> tuple[str, float]:
    """Assign a subject to the fixed 50/50 experiment variant."""
    encoded = json.dumps([subject_id, exp_id], separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], byteorder="big") % 100
    variant = "variant_a" if bucket < 50 else "variant_b"
    return variant, 0.5
