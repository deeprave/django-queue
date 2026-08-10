import threading
import time

import pytest

from django_queue.clock import (
    ClockTime,
    LocalQueueClock,
    QueueClockError,
    RedisQueueClock,
)
from tests.helpers import FakeMonotonic, FakeRedisTime

# 2026-08-03 23:33:15 UTC, the local reading the fakes below start from.
LOCAL = ClockTime(1_785_799_995)


def wait_until(condition, message, timeout=5.0):
    """Wait for a background refresh thread to make *condition* true.

    Deadline-based rather than a fixed iteration count: these wait on a daemon
    thread, and a loaded machine can take far longer to schedule it than any
    plausible number of one-millisecond sleeps allows for. A generous timeout
    keeps a genuine failure quick without making scheduling a test failure.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.001)
    pytest.fail(message)


class TestLocalQueueClock:
    def test_reports_an_instant(self):
        assert isinstance(LocalQueueClock().now(), ClockTime)


class TestRedisQueueClock:
    def test_reports_an_instant(self):
        clock = RedisQueueClock(
            FakeRedisTime((1_785_800_000, 0)), utcnow=lambda: ClockTime(1_785_800_000)
        )

        assert isinstance(clock.now(), ClockTime)

    def test_builds_the_instant_from_the_reported_integers(self):
        """At this magnitude a float on the way loses the last microsecond.

        A datetime would not, so this pins exactness rather than the absence
        of an intermediate form; that the clock builds no datetime is a
        structural property the design records and review enforces.
        """
        seconds = 2**33
        redis = FakeRedisTime((seconds, 999_999))
        clock = RedisQueueClock(redis, utcnow=lambda: ClockTime(seconds))

        assert clock.now() == ClockTime(seconds, 999_999)

    def test_derives_time_from_a_redis_to_local_utc_offset_within_refresh_window(self):
        redis = FakeRedisTime((1_785_800_000, 250_000))
        monotonic = FakeMonotonic(100.0)
        utcnow = FakeUtcNow(LOCAL + 0.25)
        clock = RedisQueueClock(redis, monotonic=monotonic, utcnow=utcnow)

        first = clock.now()
        monotonic.value = 102.5
        utcnow.value += 2.5
        second = clock.now()

        assert first == ClockTime(1_785_800_000, 250_000)
        assert second == ClockTime(1_785_800_002, 750_000)
        assert redis.time_calls == 1

    def test_refreshes_in_the_background_after_six_hundred_seconds(self):
        redis = BlockingRefreshRedis()
        monotonic = FakeMonotonic(100.0)
        utcnow = FakeUtcNow(LOCAL)
        clock = RedisQueueClock(redis, monotonic=monotonic, utcnow=utcnow)

        clock.now()
        monotonic.value = 700.0
        utcnow.value += 600
        stale = clock.now()
        assert redis.refresh_started.wait(timeout=1)
        assert stale == ClockTime(1_785_800_600)

        redis.release_refresh.set()
        assert redis.refresh_completed.wait(timeout=1)
        utcnow.value += 1

        assert clock.now() == ClockTime(1_785_800_602)
        assert redis.time_calls == 2

    def test_serialises_concurrent_initial_calibration(self):
        redis = BlockingRedisTime()
        clock = RedisQueueClock(redis, utcnow=lambda: ClockTime(1_785_800_000))

        first = threading.Thread(target=clock.now)
        first.start()
        assert redis.first_call_started.wait(timeout=1)

        second = threading.Thread(target=clock.now)
        second.start()
        assert not redis.second_call_started.wait(timeout=0.05)

        redis.release.set()
        first.join(timeout=1)
        second.join(timeout=1)

        assert redis.time_calls == 1

    def test_rejects_initial_redis_time_that_drifts_more_than_three_minutes(self):
        redis = FakeRedisTime((1_785_800_181, 0))
        clock = RedisQueueClock(redis, utcnow=lambda: ClockTime(1_785_800_000))

        with pytest.raises(QueueClockError, match="drift"):
            clock.now()

    def test_raises_a_clock_error_when_the_initial_redis_time_is_malformed(self):
        """Every untrustworthy initial calibration reports the same way."""
        clock = RedisQueueClock(
            MalformedTime(), utcnow=lambda: ClockTime(1_785_800_000)
        )

        with pytest.raises(QueueClockError, match="unusable") as raised:
            clock.now()

        assert isinstance(raised.value.__cause__, TypeError)

    def test_raises_when_initial_redis_time_is_unavailable(self):
        clock = RedisQueueClock(FailingRedisTime())

        with pytest.raises(QueueClockError, match="unavailable"):
            clock.now()

    def test_retries_after_a_background_refresh_fails_on_a_malformed_reply(self):
        """A dead refresh thread must not stop the clock refreshing for good."""
        redis = MalformedRefreshRedis()
        monotonic = FakeMonotonic(100.0)
        clock = RedisQueueClock(
            redis, monotonic=monotonic, utcnow=lambda: ClockTime(1_785_800_000)
        )
        clock.now()

        monotonic.value = 700.0
        clock.now()
        wait_until(
            lambda: redis.time_calls == 2 and not clock.refreshing,
            "Background refresh did not finish after a malformed reply",
        )

        monotonic.value = 1_300.0
        clock.now()
        wait_until(
            lambda: redis.time_calls == 3,
            "Clock did not retry after the next refresh interval",
        )

    def test_retries_after_a_background_refresh_raises_something_unexpected(self):
        """The retry contract rests on the finally, not on what is caught."""
        redis = ExplodingRefreshRedis()
        monotonic = FakeMonotonic(100.0)
        clock = RedisQueueClock(
            redis, monotonic=monotonic, utcnow=lambda: ClockTime(1_785_800_000)
        )
        clock.now()

        monotonic.value = 700.0
        clock.now()
        wait_until(
            lambda: not clock.refreshing,
            "An unexpected failure left the refresh flag set",
        )

        monotonic.value = 1_300.0
        clock.now()
        wait_until(
            lambda: redis.time_calls == 3,
            "Clock did not retry after an unexpected refresh failure",
        )

    def test_retains_the_last_good_offset_after_a_background_redis_refresh_failure(
        self,
    ):
        redis = FailingRefreshRedis()
        monotonic = FakeMonotonic(100.0)
        utcnow = FakeUtcNow(ClockTime(1_785_800_000))
        clock = RedisQueueClock(redis, monotonic=monotonic, utcnow=utcnow)
        clock.now()

        monotonic.value = 700.0
        utcnow.value += 600
        clock.now()
        assert redis.refresh_failed.wait(timeout=1)

        assert clock.now() == ClockTime(1_785_800_600)
        assert redis.time_calls == 2

        wait_until(lambda: not clock.refreshing, "Background refresh did not complete")

        monotonic.value = 1_300.0
        utcnow.value += 600
        clock.now()
        wait_until(
            lambda: redis.time_calls == 3,
            "Clock did not retry after the next refresh interval",
        )


class FakeUtcNow:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class BlockingRedisTime:
    def __init__(self):
        self.first_call_started = threading.Event()
        self.second_call_started = threading.Event()
        self.release = threading.Event()
        self.time_calls = 0
        self._lock = threading.Lock()

    def time(self):
        with self._lock:
            self.time_calls += 1
            call_number = self.time_calls
        if call_number == 1:
            self.first_call_started.set()
            self.release.wait(timeout=1)
        else:
            self.second_call_started.set()
        return 1_785_800_000, 0


class BlockingRefreshRedis:
    def __init__(self):
        self.refresh_started = threading.Event()
        self.release_refresh = threading.Event()
        self.refresh_completed = threading.Event()
        self.time_calls = 0

    def time(self):
        self.time_calls += 1
        if self.time_calls == 1:
            return 1_785_800_000, 0
        self.refresh_started.set()
        self.release_refresh.wait(timeout=1)
        self.refresh_completed.set()
        return 1_785_800_601, 0


class MalformedRefreshRedis:
    """Calibrates once, then returns a shape ClockTime rejects."""

    def __init__(self):
        self.time_calls = 0

    def time(self):
        self.time_calls += 1
        if self.time_calls == 1:
            return 1_785_800_000, 0
        return "1785800000", "0"


class MalformedTime:
    def time(self):
        return "1785800000", "0"


class ExplodingRefreshRedis:
    """Calibrates once, then fails in a way no clock handler expects."""

    def __init__(self):
        self.time_calls = 0

    def time(self):
        self.time_calls += 1
        if self.time_calls == 1:
            return 1_785_800_000, 0
        raise RuntimeError("not a clock error")


class FailingRedisTime:
    def time(self):
        raise ConnectionError("Redis is unavailable")


class FailingRefreshRedis:
    def __init__(self):
        self.time_calls = 0
        self.refresh_failed = threading.Event()

    def time(self):
        self.time_calls += 1
        if self.time_calls == 1:
            return 1_785_800_000, 0
        self.refresh_failed.set()
        raise ConnectionError("Redis is unavailable")
