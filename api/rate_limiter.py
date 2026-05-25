"""
api/rate_limiter.py — In-memory rate limiter

30 messages / hour / user_id.
No Redis needed for local testing.

Structure: {user_id: deque of float timestamps}
Check: filter to last 3600s, if len >= 30 → reject
On pass: append current timestamp

Returns True (allowed) or False (blocked).
"""

import time
from collections import deque
from typing import Dict


# Per-user timestamp queues — lives for the lifetime of the server process.
# Each value is a deque of float epoch timestamps.
_buckets: Dict[str, deque] = {}

WINDOW_SECONDS = 3600   # 1 hour sliding window
MAX_MESSAGES   = 30     # max messages per window


def is_allowed(user_id: str) -> bool:
    """
    Check if the user is within their rate limit and record the request.

    Returns True if the request is allowed, False if the limit is hit.
    Side-effect: records the current timestamp for allowed requests.
    """
    now = time.time()
    cutoff = now - WINDOW_SECONDS

    if user_id not in _buckets:
        _buckets[user_id] = deque()

    bucket = _buckets[user_id]

    # Drop timestamps older than the window
    while bucket and bucket[0] < cutoff:
        bucket.popleft()

    if len(bucket) >= MAX_MESSAGES:
        return False

    bucket.append(now)
    return True


def remaining(user_id: str) -> int:
    """Return how many messages the user has left in the current window."""
    now = time.time()
    cutoff = now - WINDOW_SECONDS
    bucket = _buckets.get(user_id, deque())
    active = sum(1 for t in bucket if t >= cutoff)
    return max(0, MAX_MESSAGES - active)
