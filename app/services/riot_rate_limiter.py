
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class _RateBucket:
    calls_1s: deque[float] = field(default_factory=deque)
    calls_120s: deque[float] = field(default_factory=deque)
    cooldown_until: float = 0.0


class RiotRateLimiter:
    """Thread-safe application limiter for local/personal Riot API keys.

    The limiter is deliberately below Riot's documented personal-key limits.
    All Riot Web API workers share it, preventing parallel player scans from
    bursting past the key's allowance.
    """

    # Keep a safety margin below the usual development/personal-key limits
    # while allowing the progressive five-game pass to fill the board faster.
    SHORT_LIMIT = 18
    SHORT_WINDOW_SECONDS = 1.0
    LONG_LIMIT = 90
    LONG_WINDOW_SECONDS = 120.0
    SAFETY_DELAY_SECONDS = 0.020

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._buckets: dict[str, _RateBucket] = {}

    @staticmethod
    def bucket_name(url: str) -> str:
        """Group platform and regional hosts conservatively by routing region."""
        host = (urlparse(url).hostname or "riot").casefold()
        prefix = host.split(".", 1)[0]

        if prefix in {
            "europe", "euw1", "eun1", "tr1", "ru", "me1",
        }:
            return "europe"
        if prefix in {
            "americas", "na1", "br1", "la1", "la2",
        }:
            return "americas"
        if prefix in {"asia", "kr", "jp1"}:
            return "asia"
        if prefix in {"sea", "oc1", "ph2", "sg2", "th2", "tw2", "vn2"}:
            return "sea"
        return host

    def acquire(self, url: str) -> None:
        bucket_name = self.bucket_name(url)

        with self._condition:
            bucket = self._buckets.setdefault(bucket_name, _RateBucket())

            while True:
                now = time.monotonic()
                self._prune(bucket, now)

                wait_for = max(bucket.cooldown_until - now, 0.0)

                if len(bucket.calls_1s) >= self.SHORT_LIMIT:
                    wait_for = max(
                        wait_for,
                        bucket.calls_1s[0] + self.SHORT_WINDOW_SECONDS - now,
                    )

                if len(bucket.calls_120s) >= self.LONG_LIMIT:
                    wait_for = max(
                        wait_for,
                        bucket.calls_120s[0] + self.LONG_WINDOW_SECONDS - now,
                    )

                if wait_for <= 0:
                    timestamp = time.monotonic()
                    bucket.calls_1s.append(timestamp)
                    bucket.calls_120s.append(timestamp)
                    return

                self._condition.wait(
                    timeout=max(wait_for + self.SAFETY_DELAY_SECONDS, 0.05)
                )

    def penalize(self, url: str, retry_after_seconds: float) -> None:
        bucket_name = self.bucket_name(url)
        delay = max(float(retry_after_seconds or 1.0), 1.0)

        with self._condition:
            bucket = self._buckets.setdefault(bucket_name, _RateBucket())
            bucket.cooldown_until = max(
                bucket.cooldown_until,
                time.monotonic() + delay + 0.25,
            )
            self._condition.notify_all()

    def reset(self) -> None:
        with self._condition:
            self._buckets.clear()
            self._condition.notify_all()

    def _prune(self, bucket: _RateBucket, now: float) -> None:
        short_cutoff = now - self.SHORT_WINDOW_SECONDS
        long_cutoff = now - self.LONG_WINDOW_SECONDS

        while bucket.calls_1s and bucket.calls_1s[0] <= short_cutoff:
            bucket.calls_1s.popleft()

        while bucket.calls_120s and bucket.calls_120s[0] <= long_cutoff:
            bucket.calls_120s.popleft()
