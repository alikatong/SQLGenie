from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from backend.rate_limit import SlidingWindowRateLimiter


class SlidingWindowRateLimiterTests(unittest.TestCase):
    def test_allows_requests_up_to_the_limit(self) -> None:
        limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=60)

        self.assertTrue(limiter.allow("user-1"))
        self.assertTrue(limiter.allow("user-1"))
        self.assertTrue(limiter.allow("user-1"))

    def test_blocks_requests_after_the_limit(self) -> None:
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60)

        self.assertTrue(limiter.allow("user-1"))
        self.assertTrue(limiter.allow("user-1"))
        self.assertFalse(limiter.allow("user-1"))

    def test_keys_are_tracked_independently(self) -> None:
        limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60)

        self.assertTrue(limiter.allow("user-1"))
        self.assertFalse(limiter.allow("user-1"))
        self.assertTrue(limiter.allow("user-2"))

    def test_window_expiry_allows_new_requests(self) -> None:
        limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=0.05)

        self.assertTrue(limiter.allow("user-1"))
        self.assertFalse(limiter.allow("user-1"))
        time.sleep(0.07)
        self.assertTrue(limiter.allow("user-1"))

    def test_zero_or_negative_limit_disables_rate_limiting(self) -> None:
        limiter = SlidingWindowRateLimiter(max_requests=0, window_seconds=60)

        self.assertTrue(limiter.allow("user-1"))
        self.assertTrue(limiter.allow("user-1"))

    def test_release_refunds_one_slot(self) -> None:
        limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60)

        self.assertTrue(limiter.allow("user-1"))
        self.assertFalse(limiter.allow("user-1"))
        limiter.release("user-1")
        self.assertTrue(limiter.allow("user-1"))

    def test_release_removes_newest_grant_without_aging_older_hits(self) -> None:
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60)

        # The second grant is refunded at t=10. A later request at t=10
        # should occupy that slot, while the original t=0 hit must expire at
        # t=70. Removing the oldest hit would leave two t=10 events and block
        # the final request.
        with patch("backend.rate_limit.time.monotonic", side_effect=[0.0, 10.0, 10.0, 10.0, 70.0]):
            self.assertTrue(limiter.allow("user-1"))
            self.assertTrue(limiter.allow("user-1"))
            limiter.release("user-1")
            self.assertTrue(limiter.allow("user-1"))
            self.assertTrue(limiter.allow("user-1"))

    def test_reset_clears_all_hits(self) -> None:
        limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60)

        self.assertTrue(limiter.allow("user-1"))
        self.assertFalse(limiter.allow("user-1"))
        limiter.reset()
        self.assertTrue(limiter.allow("user-1"))


if __name__ == "__main__":
    unittest.main()
