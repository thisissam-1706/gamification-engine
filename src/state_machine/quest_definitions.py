"""Quest definitions: the target and accepted completion kinds of a quest version."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .rules import quest_target_for


@dataclass(frozen=True)
class QuestDefinition:
    quest_id: str
    definition_version: str
    target: int
    # None means any completion kind progresses the quest.
    accepted_completion_kinds: frozenset[str] | None


_DEFINITIONS: Mapping[tuple[str, str], QuestDefinition] = MappingProxyType(
    {
        ("remediation_v1", "remediation_quests_v1"): QuestDefinition(
            quest_id="remediation_v1",
            definition_version="remediation_quests_v1",
            target=1,
            accepted_completion_kinds=frozenset({"remediation"}),
        )
    }
)


def quest_definition_for(quest_id: str, definition_version: str) -> QuestDefinition:
    """Returns a quest definition, from this registry first, else from the rule set.

    Raises KeyError for an unknown quest or version.
    """
    registered = _DEFINITIONS.get((quest_id, definition_version))
    if registered is not None:
        return registered
    return QuestDefinition(
        quest_id=quest_id,
        definition_version=definition_version,
        target=quest_target_for(quest_id, definition_version),
        accepted_completion_kinds=None,
    )
