"""Quest-update notifications: only about a quest the learner can still progress.

A quest-update message ("you are one step closer...") makes sense only while a quest is
active: assigned, not completed, not expired and before its deadline. ``build_quest_update``
returns a proposal about the learner's most urgent active quest, or ``None`` with the reason
there is nothing to say, so a caller never sends a quest update for a finished quest.

Pure: reads the learner aggregate's folded state and the ``as_of`` instant; no clock, no I/O.
"""

from dataclasses import dataclass
from datetime import datetime

from src.notifications.models import ChannelType, NotificationCategory, NotificationProposal
from src.state_machine.quest_definitions import quest_definition_for

NO_ACTIVE_QUEST = "no_active_quest"

# Learner-facing quest names (the engine stores none); unknown quests show their id.
QUEST_TITLES = {
    "lesson_starter_v1": "Weekly lesson sprint",
    "remediation_v1": "Practice quest",
}


@dataclass(frozen=True)
class QuestState:
    assignment_id: str
    quest_id: str
    title: str
    status: str  # assigned | completed | expired | overdue (assigned but past its deadline)
    progress: int
    target: int
    assigned_at: datetime | None
    deadline_at: datetime | None
    completed_at: datetime | None
    accepted_kinds: tuple[str, ...] | None  # None: any completion kind counts


def quest_states(learner, as_of: datetime) -> list[QuestState]:
    """Every quest assigned at or before ``as_of``, most urgent active quest first."""
    out: list[QuestState] = []
    for assignment_id, inst in learner.quest_instances.items():
        assigned_at = inst.get("assigned_at")
        if assigned_at is not None and assigned_at > as_of:
            continue
        quest_id = str(inst["quest_id"])
        try:
            definition = quest_definition_for(quest_id, str(inst["definition_version"]))
            target = definition.target
            kinds = definition.accepted_completion_kinds
        except KeyError:
            target, kinds = 0, None
        status = str(inst["status"])
        deadline = inst.get("deadline_at")
        if status == "assigned" and deadline is not None and deadline < as_of:
            status = "overdue"  # the expiry sweep has not run yet
        out.append(
            QuestState(
                assignment_id=assignment_id,
                quest_id=quest_id,
                title=QUEST_TITLES.get(quest_id, quest_id),
                status=status,
                progress=min(int(inst.get("progress") or 0), target) if target else int(inst.get("progress") or 0),
                target=target,
                assigned_at=assigned_at,
                deadline_at=deadline,
                completed_at=inst.get("completed_at"),
                accepted_kinds=tuple(sorted(kinds)) if kinds is not None else None,
            )
        )
    order = {"assigned": 0, "overdue": 1, "completed": 2, "expired": 3}
    far = datetime.max.replace(tzinfo=as_of.tzinfo)
    return sorted(
        out,
        key=lambda q: (order.get(q.status, 9), q.deadline_at or far, q.assignment_id),
    )


def active_quests(learner, as_of: datetime) -> list[QuestState]:
    """Quests the learner can still progress at ``as_of`` (assigned and before the deadline)."""
    return [q for q in quest_states(learner, as_of) if q.status == "assigned" and q.progress < q.target]


def build_quest_update(
    learner,
    learner_id: str,
    as_of: datetime,
    *,
    proposal_id: str,
    channel: ChannelType = ChannelType.IN_APP,
    created_at: str,
) -> tuple[NotificationProposal | None, str | None]:
    """A quest-update proposal about the most urgent active quest, or (None, reason)."""
    active = active_quests(learner, as_of)
    if not active:
        return None, NO_ACTIVE_QUEST
    quest = active[0]
    left = quest.target - quest.progress
    unit = "lesson" if quest.accepted_kinds is None else f"{quest.accepted_kinds[0]} lesson"
    body = (
        f"{quest.progress} of {quest.target} done. "
        f"{left} more {unit}{'' if left == 1 else 's'} to finish it."
    )
    return (
        NotificationProposal(
            proposal_id=proposal_id,
            learner_id=str(learner_id),
            channel=channel,
            category=NotificationCategory.QUEST_UPDATE,
            template_id="quest_update_v1",
            payload={"title": f"{quest.title}: keep going", "body": body,
                     "assignment_id": quest.assignment_id},
            created_at=created_at,
        ),
        None,
    )
