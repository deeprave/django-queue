"""Immutable queue-entry records and their JSON-safe wire representation."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class QueueEntryStatus(StrEnum):
    """The lifecycle states supported by the best-effort worker."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def next_state(self) -> frozenset[QueueEntryStatus]:
        """Return the lifecycle states this status may transition to."""
        match self:
            case QueueEntryStatus.QUEUED:
                return frozenset({QueueEntryStatus.RUNNING})
            case QueueEntryStatus.RUNNING:
                return frozenset(
                    {QueueEntryStatus.SUCCEEDED, QueueEntryStatus.FAILED, QueueEntryStatus.CANCELLED}
                )
            case QueueEntryStatus.SUCCEEDED | QueueEntryStatus.FAILED | QueueEntryStatus.CANCELLED:
                return frozenset()


def validate_json_value(value: Any) -> None:
    """Raise ``TypeError`` unless *value* can be stored in the JSON wire format."""
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise TypeError("Queue entry values must be JSON-serialisable") from exc


@dataclass(frozen=True, slots=True)
class QueueEntry:
    """An immutable, identified record of queued work and its lifecycle."""

    id: uuid.UUID
    queue: str
    status: QueueEntryStatus
    queued_at: datetime
    dispatched_at: datetime | None
    finished_at: datetime | None
    payload: Any
    result: Any | None
    error: dict[str, str] | None

    def __post_init__(self) -> None:
        if not isinstance(self.status, QueueEntryStatus):
            raise TypeError("Queue entry status must be a QueueEntryStatus")
        if self.id.version != 7:
            raise ValueError("Queue entry IDs must be UUIDv7 values")
        if not self.queue:
            raise ValueError("Queue entry queue names must not be empty")
        for value in (self.payload, self.result, self.error):
            validate_json_value(value)

    @classmethod
    def create(cls, *, queue: str, payload: Any, queued_at: datetime | None = None) -> QueueEntry:
        """Create a newly queued entry with a queue-owned UUIDv7 and timestamp."""
        return cls(
            id=uuid.uuid7(),
            queue=queue,
            status=QueueEntryStatus.QUEUED,
            queued_at=queued_at or datetime.now(UTC),
            dispatched_at=None,
            finished_at=None,
            payload=payload,
            result=None,
            error=None,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the complete JSON-compatible durable representation."""
        return {
            "id": str(self.id),
            "queue": self.queue,
            "status": self.status.value,
            "queued_at": self.queued_at.isoformat(),
            "dispatched_at": self.dispatched_at.isoformat() if self.dispatched_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "payload": self.payload,
            "result": self.result,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> QueueEntry:
        """Rebuild an entry from its JSON-decoded durable representation."""
        return cls(
            id=uuid.UUID(value["id"]),
            queue=value["queue"],
            status=QueueEntryStatus(value["status"]),
            queued_at=datetime.fromisoformat(value["queued_at"]),
            dispatched_at=datetime.fromisoformat(value["dispatched_at"]) if value["dispatched_at"] else None,
            finished_at=datetime.fromisoformat(value["finished_at"]) if value["finished_at"] else None,
            payload=value["payload"],
            result=value["result"],
            error=value["error"],
        )
