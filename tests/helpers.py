from datetime import UTC, datetime
from uuid import UUID

FIXED_UUID7 = UUID("0198babb-3bce-7f81-8c43-3c1d99f475a9")


class FixedClock:
    def __init__(self, timestamp: datetime | None = None):
        self.timestamp = timestamp or datetime(2026, 8, 6, 12, 0, tzinfo=UTC)

    def now(self):
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
