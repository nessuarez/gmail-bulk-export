"""Rate limiting and retry policy for the Gmail API.

Gmail enforces a per-user quota of 250 quota units per second. Both
`messages.list` and `messages.get` cost 5 units, which puts the practical
ceiling at ~50 messages/second for a single mailbox.

The previous approach — `time.sleep(random.uniform(1, 5))` after every batch of
20 — cost about 3 seconds per 20 messages regardless of what the API was
actually doing, and did nothing at all when the API *did* push back. This module
replaces both halves of that: a token bucket paces requests towards the quota,
and `gmail_retry` reacts to real rate-limit responses instead of retrying every
exception ten times.
"""

import logging
import random
import threading
import time

from googleapiclient.errors import HttpError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from gmail_bulk_export.config import get_config

logger = logging.getLogger(__name__)

# Retryable HTTP statuses. 403 is only retryable for quota reasons (see below);
# a 403 caused by missing domain-wide delegation must fail immediately.
_RETRYABLE_STATUSES = (429, 500, 502, 503, 504)
_QUOTA_REASONS = (
    "ratelimitexceeded",
    "userratelimitexceeded",
    "quotaexceeded",
    "backenderror",
)


class TokenBucket:
    """Thread-safe token bucket used to pace API calls towards a target rate."""

    def __init__(self, rate: float, capacity: float = None):
        self.rate = float(rate)
        self.capacity = float(capacity if capacity is not None else rate)
        self._tokens = self.capacity
        self._updated_at = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> float:
        """Blocks until `tokens` are available. Returns the time spent waiting."""
        # A single request must never deadlock against a bucket smaller than it.
        tokens = min(float(tokens), self.capacity)
        waited = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._updated_at) * self.rate
                )
                self._updated_at = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return waited
                deficit = tokens - self._tokens
                sleep_for = deficit / self.rate
            time.sleep(sleep_for)
            waited += sleep_for


def _quota_reason(error: HttpError) -> str:
    """Extracts the lowercased Google API error reason, or '' if absent."""
    try:
        details = error.error_details  # googleapiclient >= 2.x
    except Exception:
        details = None
    if isinstance(details, list):
        for detail in details:
            if isinstance(detail, dict) and detail.get("reason"):
                return str(detail["reason"]).lower()
    # Fall back to the raw payload, which always carries the reason string.
    return str(getattr(error, "content", b"") or b"").lower()


def is_retryable(exc: BaseException) -> bool:
    """True for transient Gmail failures worth retrying."""
    if isinstance(exc, (TimeoutError, ConnectionError, BrokenPipeError)):
        return True
    if not isinstance(exc, HttpError):
        return False

    status = getattr(getattr(exc, "resp", None), "status", None)
    if status in _RETRYABLE_STATUSES:
        return True
    if status == 403:
        reason = _quota_reason(exc)
        return any(quota_reason in reason for quota_reason in _QUOTA_REASONS)
    return False


def _log_retry(retry_state):
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        "Retry %d of %s after %s",
        retry_state.attempt_number,
        retry_state.fn.__name__ if retry_state.fn else "?",
        exc,
    )


def gmail_retry(func):
    """Retries only transient Gmail failures, with exponential backoff + jitter.

    Everything else (404, malformed request, auth failure) raises straight away
    instead of burning ten backoffs on an error that will never succeed.
    """
    attempts = int(get_config("retry_attempts", 8) or 8)
    wait_max = int(get_config("retry_wait_max", 64) or 64)
    return retry(
        retry=retry_if_exception(is_retryable),
        wait=wait_exponential_jitter(initial=1, max=max(wait_max, 8), jitter=2),
        stop=stop_after_attempt(attempts),
        before_sleep=_log_retry,
        reraise=True,
    )(func)


_bucket_lock = threading.Lock()
_buckets = {}


def get_bucket(name: str = "gmail") -> TokenBucket:
    """Returns the token bucket for `name`, built from `messages_per_second`.

    Callers should pass the *mailbox* as the name. Gmail's 250 units/second is a
    per-user quota, so two mailboxes do not compete for the same allowance;
    sharing one global bucket between them would cap the whole run at a single
    mailbox's rate no matter how many run concurrently.
    """
    with _bucket_lock:
        bucket = _buckets.get(name)
        if bucket is None:
            rate = float(get_config("messages_per_second", 40) or 40)
            bucket = TokenBucket(rate=rate, capacity=max(rate, 1.0))
            _buckets[name] = bucket
            logger.info("Token bucket '%s' at %.1f messages/s", name, rate)
        return bucket


def pace(tokens: float = 1.0, name: str = "gmail") -> float:
    """Convenience wrapper: waits for `tokens` on `name`'s bucket."""
    return get_bucket(name or "gmail").acquire(tokens)


def jittered_pause(base: float = 0.0) -> None:
    """Small randomized pause used to de-synchronize worker threads."""
    if base > 0:
        time.sleep(random.uniform(0, base))
