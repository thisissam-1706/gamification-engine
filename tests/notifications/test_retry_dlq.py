"""AT-10: Retry & DLQ (M5)

Given a notification delivery attempt fails due to a provider error, when retries are exhausted, then the message routes to the DLQ and is recoverable after the channel failure is resolved.
"""
import pytest

@pytest.mark.skip(reason="Notifications are outside this OSS spike.")
def test_retry_dlq():
    pass
