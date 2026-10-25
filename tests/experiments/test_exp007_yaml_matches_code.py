"""exp_007.yaml must document the assignment hash that assign() really uses.

The yaml is read as text (pyyaml is not a runtime dependency). The bucket
rule's numbers are parsed from the documented function, then recomputed here
independently of assign(), so a drift in either the doc or the code fails.
"""

import hashlib
import json
import re
from pathlib import Path

import pytest

from src.experiments.assignment import assign

YAML = Path(__file__).resolve().parents[2] / "experiments" / "exp_007.yaml"
SUBJECTS = ["L1", "learner-001", "learner-002", "learner-004", "synthetic-0"]


def _text() -> str:
    return YAML.read_text(encoding="utf-8")


def _function_line() -> str:
    return next(
        line for line in _text().splitlines() if line.strip().startswith("function:")
    )


def _documented_rule() -> tuple[int, str, int, int]:
    """(prefix bytes, byte order, modulus, variant_a threshold) as documented."""
    line = _function_line()
    prefix = re.search(r"\[:(\d+)\]", line)
    modulus = re.search(r"mod (\d+)", line)
    threshold = re.search(r"bucket < (\d+) -> variant_a", line)
    assert prefix and modulus and threshold, line
    assert "big-endian" in line and "'big'" in line, line
    return int(prefix.group(1)), "big", int(modulus.group(1)), int(threshold.group(1))


def test_yaml_keeps_hash_v2_and_drops_concatenation_formula():
    text = _text()
    assert 'algorithm: "hash_v2"' in text
    assert "json" in _function_line().lower()
    assert "||" not in text


def test_documented_rule_is_first_8_bytes_big_endian_mod_100_below_50():
    assert _documented_rule() == (8, "big", 100, 50)


@pytest.mark.parametrize("subject_id", SUBJECTS)
def test_documented_rule_matches_assign(subject_id):
    prefix, byteorder, modulus, threshold = _documented_rule()
    encoded = json.dumps([subject_id, "exp_007"], separators=(",", ":")).encode("utf-8")
    bucket = int.from_bytes(hashlib.sha256(encoded).digest()[:prefix], byteorder) % modulus
    expected = "variant_a" if bucket < threshold else "variant_b"

    assert assign(subject_id, "exp_007") == (expected, 0.5)


def test_samples_cover_both_variants():
    # Otherwise the parametrized check could pass with a constant assign().
    assert {assign(subject_id, "exp_007")[0] for subject_id in SUBJECTS} == {
        "variant_a",
        "variant_b",
    }
