"""exp_007 eligibility rules shared by the streak-risk sweep and the assignment store."""

from datetime import datetime, timedelta

from src.notifications.models import ChannelType, LearnerPreferences, NotificationCategory

# exp_007 exclusion: platform tenure (at minus the learner's Created timestamp) under this.
MIN_TENURE = timedelta(days=3)

# Skip reasons.
EXCLUDED_TENURE = "excluded_tenure"
EXCLUDED_OPTED_OUT = "excluded_opted_out"
MISSING_PREFERENCES = "missing_preferences"


def evaluate_eligibility(
    created_on: datetime, prefs: LearnerPreferences | None, at: datetime
) -> tuple[bool, str | None]:
    """Returns (eligible, reason); reason is None when eligible.

    Order: missing preferences, then tenure, then opt-out of push or streak_risk.
    """
    if prefs is None:
        return False, MISSING_PREFERENCES
    if at - created_on < MIN_TENURE:
        return False, EXCLUDED_TENURE
    if (
        ChannelType.PUSH in prefs.opted_out_channels
        or NotificationCategory.STREAK_RISK in prefs.opted_out_categories
    ):
        return False, EXCLUDED_OPTED_OUT
    return True, None
