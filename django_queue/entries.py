"""Immutable queue-entry records and their JSON-safe wire representation."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
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
                    {
                        QueueEntryStatus.SUCCEEDED,
                        QueueEntryStatus.FAILED,
                        QueueEntryStatus.CANCELLED,
                    }
                )
            case (
                QueueEntryStatus.SUCCEEDED
                | QueueEntryStatus.FAILED
                | QueueEntryStatus.CANCELLED
            ):
                return frozenset()


_WIRE_DECODERS: Mapping[str, Callable[[Any], Any]] = {
    "id": uuid.UUID,
    "status": QueueEntryStatus,
    "queued_at": datetime.fromisoformat,
    "dispatched_at": datetime.fromisoformat,
    "finished_at": datetime.fromisoformat,
}


def _encode_wire_value(name: str, value: Any) -> Any:
    """Render a field value in its JSON-compatible durable form."""
    if name not in _WIRE_DECODERS or value is None:
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    return value.isoformat()


def _decode_wire_value(name: str, value: Any) -> Any:
    """Restore a field value from its durable form."""
    decoder = _WIRE_DECODERS.get(name)
    if decoder is None or value is None:
        return value
    return decoder(value)


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
        for field in fields(self):
            # A field either has a wire conversion or is stored as-is, in which
            # case it must already be JSON-safe. That covers the payload and any
            # field a subclass declares.
            if field.name not in _WIRE_DECODERS:
                validate_json_value(getattr(self, field.name))

    @classmethod
    def create(
        cls, *, queue: str, payload: Any, queued_at: datetime | None = None
    ) -> QueueEntry:
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
            field.name: _encode_wire_value(field.name, getattr(self, field.name))
            for field in fields(self)
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> QueueEntry:
        """Rebuild an entry from its JSON-decoded durable representation."""
        return cls(
            **{
                field.name: _decode_wire_value(field.name, value[field.name])
                for field in fields(cls)
                if field.init and field.name in value
            }
        )
