from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from django_queue.entries import QueueEntry

FIXED_UUID7 = UUID("0198babb-3bce-7f81-8c43-3c1d99f475a9")


@dataclass(frozen=True, slots=True)
class CustomQueueEntry(QueueEntry):
    kind: str = "unset"

    @classmethod
    def create(
        cls, *, queue: str, payload: Any, queued_at: datetime | None = None
    ) -> CustomQueueEntry:
        entry = QueueEntry.create(queue=queue, payload=payload, queued_at=queued_at)
        return cls(
            id=entry.id,
            queue=entry.queue,
            status=entry.status,
            queued_at=entry.queued_at,
            dispatched_at=entry.dispatched_at,
            finished_at=entry.finished_at,
            payload=entry.payload,
            result=entry.result,
            error=entry.error,
            kind="task",
        )

    def to_dict(self) -> dict[str, Any]:
        return {**QueueEntry.to_dict(self), "kind": self.kind}

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CustomQueueEntry:
        entry = QueueEntry.from_dict(value)
        return cls(
            id=entry.id,
            queue=entry.queue,
            status=entry.status,
            queued_at=entry.queued_at,
            dispatched_at=entry.dispatched_at,
            finished_at=entry.finished_at,
            payload=entry.payload,
            result=entry.result,
            error=entry.error,
            kind=value["kind"],
        )


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
