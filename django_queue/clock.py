"""Queue clocks with a Redis-authoritative implementation."""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock, Thread
from typing import Protocol, overload

MAX_CLOCK_DRIFT_SECONDS = 180
MICROSECONDS_PER_SECOND = 1_000_000
_SECONDS_PER_DAY = 86_400
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, order=True)
class ClockTime:
    """An instant, as whole seconds and microseconds since the Unix epoch.

    Durations are plain counts of seconds; this type is only ever a point in
    time. It does not coerce to a number, so an instant cannot silently stand
    in for a duration — reach for `to_timestamp()` where a number is wanted.
    """

    seconds: int
    microseconds: int = 0

    def __post_init__(self) -> None:
        for name in ("seconds", "microseconds"):
            # `type(...) is int` rather than isinstance: bool is an int, and a
            # flag standing in for a component is exactly the confusion this
            # type exists to catch.
            if type(getattr(self, name)) is not int:
                raise TypeError(f"Clock time {name} must be a whole number")
        if not 0 <= self.microseconds < MICROSECONDS_PER_SECOND:
            raise ValueError(
                "Clock time microseconds must be at least 0 and less than "
                f"{MICROSECONDS_PER_SECOND}"
            )
        if self.seconds < 0:
            raise ValueError("Clock time cannot describe an instant before the epoch")

    @classmethod
    def from_timeval(cls, seconds: int, microseconds: int) -> ClockTime:
        """Build from the second and microsecond pair a Redis TIME reply gives."""
        return cls(seconds, microseconds)

    @classmethod
    def from_timestamp(cls, timestamp: float) -> ClockTime:
        """Build from a count of seconds since the epoch."""
        if type(timestamp) not in (int, float):
            raise TypeError("Clock time timestamp must be an int or float")
        if not math.isfinite(timestamp):
            raise ValueError("Clock time requires a finite count of seconds")
        if timestamp < 0:
            raise ValueError("Clock time cannot describe an instant before the epoch")
        seconds = int(timestamp)
        microseconds = round((timestamp - seconds) * MICROSECONDS_PER_SECOND)
        if microseconds == MICROSECONDS_PER_SECOND:
            seconds, microseconds = seconds + 1, 0
        return cls(seconds, microseconds)

    @classmethod
    def from_datetime(cls, moment: datetime) -> ClockTime:
        """Build from a timezone-aware datetime; a naive one names no instant.

        Read through a timedelta rather than a float timestamp, so the exact
        microseconds a datetime already carries survive at any magnitude.
        """
        if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
            raise ValueError("Clock time requires a timezone-aware datetime")
        elapsed = moment - _EPOCH
        seconds = elapsed.days * _SECONDS_PER_DAY + elapsed.seconds
        if seconds < 0:
            raise ValueError("Clock time cannot describe an instant before the epoch")
        return cls(seconds, elapsed.microseconds)

    def to_timestamp(self) -> float:
        """Render as a count of seconds since the epoch, the durable form."""
        return self.seconds + self.microseconds / MICROSECONDS_PER_SECOND

    def to_datetime(self) -> datetime:
        """Render as an aware UTC datetime, for calendar behaviour."""
        return datetime.fromtimestamp(self.seconds, UTC).replace(
            microsecond=self.microseconds
        )

    def _total_microseconds(self) -> int:
        return self.seconds * MICROSECONDS_PER_SECOND + self.microseconds

    def __add__(self, duration: float) -> ClockTime:
        """Shift by a count of seconds, yielding another instant."""
        if type(duration) not in (int, float):
            return NotImplemented
        if not math.isfinite(duration):
            raise ValueError("Clock time requires a finite duration in seconds")
        total = self._total_microseconds() + round(duration * MICROSECONDS_PER_SECOND)
        if total < 0:
            raise ValueError("Clock time cannot describe an instant before the epoch")
        return type(self)(*divmod(total, MICROSECONDS_PER_SECOND))

    __radd__ = __add__

    @overload
    def __sub__(self, other: ClockTime) -> float: ...

    @overload
    def __sub__(self, other: float) -> ClockTime: ...

    def __sub__(self, other: ClockTime | float) -> float | ClockTime:
        """Measure against another instant, or shift back by a count of seconds."""
        if isinstance(other, ClockTime):
            elapsed = self._total_microseconds() - other._total_microseconds()
            return elapsed / MICROSECONDS_PER_SECOND
        if type(other) not in (int, float):
            return NotImplemented
        return self + -other


def elapsed_time(start: ClockTime | None, end: ClockTime | None) -> float | None:
    """Time from *start* to *end* in seconds, or None where that has no answer.

    A count of seconds carrying its microseconds, not a whole number of them:
    `ClockTime` subtraction works in integer microseconds and divides once, so a
    sub-second span reports as such -- 137 microseconds is 0.000137, not 0.

    Absent when either instant is missing, and absent when they contradict: a
    clock that moved backwards -- as a recalibrated Redis clock may -- can leave
    *end* before *start*, and a negative elapsed time is not a smaller duration
    but a meaningless one.
    """
    if start is None or end is None:
        return None
    seconds = end - start
    return None if seconds < 0 else seconds


class QueueClock(Protocol):
    def now(self) -> ClockTime: ...


class LocalQueueClock:
    """UTC wall-clock fallback used by in-memory queues."""

    def now(self) -> ClockTime:
        return ClockTime.from_timestamp(time.time())


# Stateless, so one instance serves every fallback in the package.
DEFAULT_CLOCK = LocalQueueClock()


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
        utcnow: Callable[[], ClockTime] = lambda: ClockTime.from_timestamp(time.time()),
    ) -> None:
        self._redis = redis
        self._refresh_interval = refresh_interval
        self._monotonic = monotonic
        self._utcnow = utcnow
        self._last_refresh_attempt: float | None = None
        self._offset: float | None = None
        self._refreshing = False
        self._lock = Lock()

    @property
    def refreshing(self) -> bool:
        """Return whether a background calibration refresh is in flight.

        Advisory: read without the calibration lock, so it answers for the
        moment it was asked and may be stale by the time a caller acts on it.
        Useful for observing that a refresh is under way, not for coordinating
        with one.
        """
        return self._refreshing

    def now(self) -> ClockTime:
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

    def _read_calibration(self) -> tuple[float, float]:
        local_time = self._utcnow()
        try:
            seconds, microseconds = self._redis.time()
        except Exception as exc:
            raise QueueClockError("Redis TIME is unavailable") from exc

        offset = ClockTime.from_timeval(seconds, microseconds) - local_time
        if abs(offset) > MAX_CLOCK_DRIFT_SECONDS:
            raise QueueClockError(
                "Redis and local UTC clocks exceed the maximum permitted drift"
            )
        return offset, self._monotonic()

    def _set_calibration(self, calibration: tuple[float, float]) -> None:
        # Named rather than unpacked: both are counts of seconds, one a
        # wall-clock offset and one a monotonic reading, so a transposition
        # would type-check and silently break refreshing.
        offset, read_at = calibration
        self._offset = offset
        self._last_refresh_attempt = read_at

    def _refresh_in_background(self) -> None:
        # Any failure retains the last good offset and retries at the next
        # interval, so the flag is cleared in a finally: a refresh that died
        # without clearing it would stop this clock refreshing for good, while
        # every reported time still looked ordinary.
        try:
            calibration = self._read_calibration()
        except Exception as exc:
            logger.warning(
                "Unable to refresh Redis queue time; retaining the last known offset",
                exc_info=exc,
            )
        else:
            with self._lock:
                self._set_calibration(calibration)
        finally:
            with self._lock:
                self._refreshing = False
