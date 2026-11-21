"""Shared test isolation."""

import pytest

from src.state_machine.rules import _REGISTRY


@pytest.fixture(autouse=True)
def _restore_rule_registry():
    """Puts the process-wide rule-set registry back after every test.

    Opening a demo sandbox registers the decay rule sets (v2, v3), and some tests register
    rule sets of their own. Without this, whichever test ran first would decide what every
    later test sees in RULESETS (for example "v2 is not in the default registry").
    """
    saved = dict(_REGISTRY)
    yield
    _REGISTRY.clear()
    _REGISTRY.update(saved)
