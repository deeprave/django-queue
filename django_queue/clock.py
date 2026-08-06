"""Queue clocks with a Redis-authoritative implementation."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Lock, Thread
from typing import Protocol

MAX_CLOCK_DRIFT_SECONDS = 180

logger = logging.getLogger(__name__)


class QueueClock(Protocol):
    def now(self) -> datetime: ...


class LocalQueueClock:
    """UTC wall-clock fallback used by in-memory queues."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class RedisTimeClient(Protocol):
    def time(self) -> tuple[int, int]: ...


class QueueClockError(RuntimeError):
    """Raised when Redis-backed queue time cannot be trusted."""


class RedisQueueClock:
    """Derive timestamps from a periodically refreshed Redis-to-local UTC offset."""

    def __init__(
        self,
        redis: RedisTimeClient,
        *,
        refresh_interval: float = 600,
        monotonic: Callable[[], float] = time.monotonic,
        utcnow: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._redis = redis
        self._refresh_interval = refresh_interval
        self._monotonic = monotonic
        self._utcnow = utcnow
        self._last_refresh_attempt: float | None = None
        self._offset: timedelta | None = None
        self._refreshing = False
        self._lock = Lock()

    def now(self) -> datetime:
        with self._lock:
            monotonic_now = self._monotonic()
            if self._offset is None:
                self._set_calibration(self._read_calibration())
            elif self._needs_refresh(monotonic_now) and not self._refreshing:
                self._refreshing = True
                self._last_refresh_attempt = monotonic_now
                Thread(target=self._refresh_in_background, daemon=True).start()

            assert self._offset is not None
            return self._utcnow() + self._offset

    def _needs_refresh(self, monotonic_now: float) -> bool:
        return (
            self._last_refresh_attempt is None
            or monotonic_now - self._last_refresh_attempt >= self._refresh_interval
        )

    def _read_calibration(self) -> tuple[timedelta, float]:
        local_time = self._utcnow()
        try:
            seconds, microseconds = self._redis.time()
        except Exception as exc:
            raise QueueClockError("Redis TIME is unavailable") from exc

        redis_time = datetime.fromtimestamp(seconds, UTC).replace(microsecond=microseconds)
        offset = redis_time - local_time
        if abs(offset.total_seconds()) > MAX_CLOCK_DRIFT_SECONDS:
            raise QueueClockError("Redis and local UTC clocks exceed the maximum permitted drift")
        return offset, self._monotonic()

    def _set_calibration(self, calibration: tuple[timedelta, float]) -> None:
        self._offset, self._last_refresh_attempt = calibration

    def _refresh_in_background(self) -> None:
        try:
            calibration = self._read_calibration()
        except QueueClockError as exc:
            logger.warning("Unable to refresh Redis queue time; retaining the last known offset", exc_info=exc)
            with self._lock:
                self._refreshing = False
        else:
            with self._lock:
                self._set_calibration(calibration)
                self._refreshing = False
