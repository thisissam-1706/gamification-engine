"""Retry scheduler and DLQ dispatch management."""

from collections.abc import Callable
import time
from typing import Any

from src.notifications.channels.base import (
    BaseChannelAdapter,
    PermanentProviderError,
    TransientProviderError,
)
from src.notifications.models import NotificationProposal


class RetryScheduler:
    """Handles transient provider delivery retries with exponential backoff."""

    def __init__(
        self,
        max_retries: int = 3,
        backoff_delay_sec: float = 0.0,
        sleep_fn: Callable[[float], Any] | None = None,
    ) -> None:
        self.max_retries = max_retries
        self.backoff_delay_sec = backoff_delay_sec
        self.sleep_fn = sleep_fn or time.sleep

    def deliver_with_retry(
        self,
        adapter: BaseChannelAdapter,
        proposal: NotificationProposal,
    ) -> tuple[bool, int, str | None]:
        """Attempts delivery with retry on transient failures.

        Returns:
            (success, attempts_made, failure_reason)
        """
        attempts = 0
        last_error = None

        while attempts < self.max_retries:
            attempts += 1
            try:
                success = adapter.send(proposal)
                if success:
                    return True, attempts, None
                last_error = "adapter_returned_false"
            except TransientProviderError as exc:
                last_error = str(exc)
            except PermanentProviderError as exc:
                # Permanent failure bypasses remaining retry attempts
                return False, attempts, f"permanent_failure: {exc}"

            # Exponential backoff: base_delay * (2 ** (attempts - 1))
            if self.backoff_delay_sec > 0 and attempts < self.max_retries:
                delay = self.backoff_delay_sec * (2 ** (attempts - 1))
                self.sleep_fn(delay)

        return False, attempts, f"provider_exhausted: {last_error}"
