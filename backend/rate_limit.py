from __future__ import annotations

import threading
import time
from collections import deque


class SlidingWindowRateLimiter:
    """Small in-process sliding-window limiter with no external dependencies.

    State lives only in this process's memory: when the service runs with
    multiple workers/processes each worker keeps its own independent counters,
    so effective limits scale with the number of processes. Use a shared store
    (e.g. Redis) if exact cross-process enforcement is required.
    """

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window_seconds = float(window_seconds)
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.max_requests <= 0 or self.window_seconds <= 0:
            return True

        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            events = self._hits.get(key)
            if events is None:
                events = deque()
                self._hits[key] = events
            while events and events[0] < cutoff:
                events.popleft()
            if len(events) >= self.max_requests:
                return False
            events.append(now)
            return True

    def release(self, key: str) -> None:
        """Return one previously granted slot for a key.

        Used to refund quota for requests that failed before consuming any
        costly work (e.g. invalid input that never reached the model), so
        client-side errors do not silently exhaust a user's budget.
        """
        if self.max_requests <= 0 or self.window_seconds <= 0:
            return

        with self._lock:
            events = self._hits.get(key)
            if not events:
                return
            now = time.monotonic()
            cutoff = now - self.window_seconds
            while events and events[0] < cutoff:
                events.popleft()
            if events:
                events.pop()
            if not events:
                self._hits.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


__all__ = ["SlidingWindowRateLimiter"]
