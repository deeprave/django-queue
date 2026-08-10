from dataclasses import dataclass
from uuid import UUID

from django_queue.clock import ClockTime
from django_queue.entries import QueueEntry

FIXED_UUID7 = UUID("0198babb-3bce-7f81-8c43-3c1d99f475a9")
# 2026-08-06 12:00:00 UTC
FIXED_CLOCK_TIME = ClockTime(1_786_032_000)


@dataclass(frozen=True, slots=True)
class CustomQueueEntry(QueueEntry):
    """An entry subclass adding one field and nothing else."""

    kind: str = "task"


class FixedClock:
    def __init__(self, timestamp: ClockTime | None = None):
        self.timestamp = timestamp or FIXED_CLOCK_TIME

    def now(self) -> ClockTime:
        return self.timestamp


class FakeMonotonic:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class FakeRedisTime:
    def __init__(self, *values):
        self.values = iter(values)
        self.time_calls = 0

    def time(self):
        self.time_calls += 1
        return next(self.values)
