"""AT-11: Opt-Out Enforcement (M5)

Given L1 has opted out of push, when a candidate is generated for L1, then Notification Failure is emitted with failure_reason = opt_out, not a delivery attempt.
"""

from src.notifications.channels.push import SimulatedPushAdapter
from src.notifications.dispatcher import NotificationDispatcher
from src.notifications.models import (
    ChannelType,
    LearnerPreferences,
    NotificationCategory,
    NotificationProposal,
    NotificationStatus,
)
from src.notifications.store import NotificationStore


def test_opt_out_enforcement(tmp_path):
    store = NotificationStore(str(tmp_path / "notif.db"))
    push = SimulatedPushAdapter()
    dispatcher = NotificationDispatcher(store=store, adapters={ChannelType.PUSH: push})

    # Learner L1 has opted out of Push channel
    prefs = LearnerPreferences(
        learner_id="L1",
        opted_out_channels={ChannelType.PUSH},
    )

    prop = NotificationProposal(
        proposal_id="p_optout_01",
        learner_id="L1",
        channel=ChannelType.PUSH,
        category=NotificationCategory.STREAK_RISK,
        template_id="streak_warning_v1",
    )

    res = dispatcher.dispatch(prop, prefs=prefs)

    # Dispatched proposal is suppressed due to opt-out
    assert res.status == NotificationStatus.SUPPRESSED
    assert res.reason == "opt_out"

    # Zero delivery attempts made
    assert push.attempts_seen == 0
    assert len(push.delivered_messages) == 0

    store.close()
