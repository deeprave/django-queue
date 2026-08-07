import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from django_queue.clock import LocalQueueClock, QueueClockError, RedisQueueClock
from tests.helpers import FakeMonotonic, FakeRedisTime


class TestLocalQueueClock:
    def test_returns_an_aware_utc_timestamp(self):
        timestamp = LocalQueueClock().now()

        assert timestamp.tzinfo is UTC


class TestRedisQueueClock:
    def test_derives_time_from_a_redis_to_local_utc_offset_within_refresh_window(self):
        redis = FakeRedisTime((1_785_800_000, 250_000))
        monotonic = FakeMonotonic(100.0)
        utcnow = FakeUtcNow(datetime(2026, 8, 3, 23, 33, 15, 250_000, tzinfo=UTC))
        clock = RedisQueueClock(redis, monotonic=monotonic, utcnow=utcnow)

        first = clock.now()
        monotonic.value = 102.5
        utcnow.value += timedelta(seconds=2.5)
        second = clock.now()

        assert first == datetime(2026, 8, 3, 23, 33, 20, 250_000, tzinfo=UTC)
        assert second == datetime(2026, 8, 3, 23, 33, 22, 750_000, tzinfo=UTC)
        assert redis.time_calls == 1

    def test_refreshes_in_the_background_after_six_hundred_seconds(self):
        redis = BlockingRefreshRedis()
        monotonic = FakeMonotonic(100.0)
        utcnow = FakeUtcNow(datetime(2026, 8, 3, 23, 33, 15, tzinfo=UTC))
        clock = RedisQueueClock(redis, monotonic=monotonic, utcnow=utcnow)

        clock.now()
        monotonic.value = 700.0
        utcnow.value += timedelta(seconds=600)
        stale = clock.now()
        assert redis.refresh_started.wait(timeout=1)
        assert stale == datetime(2026, 8, 3, 23, 43, 20, tzinfo=UTC)

        redis.release_refresh.set()
        assert redis.refresh_completed.wait(timeout=1)
        utcnow.value += timedelta(seconds=1)

        assert clock.now() == datetime(2026, 8, 3, 23, 43, 22, tzinfo=UTC)
        assert redis.time_calls == 2

    def test_serialises_concurrent_initial_calibration(self):
        redis = BlockingRedisTime()
        clock = RedisQueueClock(
            redis, utcnow=lambda: datetime(2026, 8, 3, 23, 33, 20, tzinfo=UTC)
        )

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
        clock = RedisQueueClock(
            redis, utcnow=lambda: datetime(2026, 8, 3, 23, 33, 20, tzinfo=UTC)
        )

        with pytest.raises(QueueClockError, match="drift"):
            clock.now()

    def test_raises_when_initial_redis_time_is_unavailable(self):
        clock = RedisQueueClock(FailingRedisTime())

        with pytest.raises(QueueClockError, match="unavailable"):
            clock.now()

    def test_retains_the_last_good_offset_after_a_background_redis_refresh_failure(
        self,
    ):
        redis = FailingRefreshRedis()
        monotonic = FakeMonotonic(100.0)
        utcnow = FakeUtcNow(datetime(2026, 8, 3, 23, 33, 20, tzinfo=UTC))
        clock = RedisQueueClock(redis, monotonic=monotonic, utcnow=utcnow)
        clock.now()

        monotonic.value = 700.0
        utcnow.value += timedelta(seconds=600)
        clock.now()
        assert redis.refresh_failed.wait(timeout=1)

        assert clock.now() == datetime(2026, 8, 3, 23, 43, 20, tzinfo=UTC)
        assert redis.time_calls == 2

        for _ in range(100):
            if not clock._refreshing:
                break
            time.sleep(0.001)
        else:
            pytest.fail("Background refresh did not complete")

        monotonic.value = 1_300.0
        utcnow.value += timedelta(seconds=600)
        clock.now()
        for _ in range(100):
            if redis.time_calls == 3:
                break
            time.sleep(0.001)
        else:
            pytest.fail("Clock did not retry after the next refresh interval")


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
