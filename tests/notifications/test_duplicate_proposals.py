"""AT-9: Duplicate Notification Proposals (M5)

Given proposal_id = p_001 was already generated for L1, when the policy attempts to generate a second candidate for the same trigger, then it is deduplicated by proposal_id before dispatch.
"""

from src.notifications.channels.in_app import InAppChannelAdapter
from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.models import (
    ChannelType,
    LearnerPreferences,
    NotificationCategory,
    NotificationProposal,
    NotificationStatus,
)
from src.notifications.store import NotificationStore


def test_duplicate_proposals(tmp_path):
    store = NotificationStore(str(tmp_path / "notif.db"))
    in_app = InAppChannelAdapter()
    dispatcher = NotificationDispatcher(store=store, adapters={ChannelType.IN_APP: in_app})

    proposal = NotificationProposal(
        proposal_id="p_001",
        learner_id="L1",
        channel=ChannelType.IN_APP,
        category=NotificationCategory.STREAK_RISK,
        template_id="streak_warning_v1",
        payload={"streak": 5},
    )

    prefs = LearnerPreferences(learner_id="L1")

    # First dispatch succeeds
    res1 = dispatcher.dispatch(proposal, prefs=prefs, now_utc="2026-10-12T12:00:00Z")
    assert res1.status == NotificationStatus.DISPATCHED
    assert len(in_app.delivered_messages) == 1

    # Second dispatch with identical proposal_id is deduplicated and suppressed
    res2 = dispatcher.dispatch(proposal, prefs=prefs, now_utc="2026-10-12T12:00:00Z")
    assert res2.status == NotificationStatus.SUPPRESSED
    assert res2.reason == "duplicate_proposal"
    # No second delivery attempt occurred
    assert len(in_app.delivered_messages) == 1

    store.close()
