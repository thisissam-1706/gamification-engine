"""Configuration for the remediation policy."""

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class RemediationConfig:
    frequency_window: timedelta = timedelta(days=14)
    quest_deadline: timedelta = timedelta(days=7)
    quest_id: str = "remediation_v1"
    quest_definition_version: str = "remediation_quests_v1"
    policy_version: str = "remediation_policy_v1"
