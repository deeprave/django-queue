"""Immutable queue-entry records and their JSON-safe wire representation."""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Callable, Mapping
from dataclasses import MISSING, dataclass, fields
from enum import StrEnum
from typing import Any

from django_queue.clock import DEFAULT_CLOCK, ClockTime, elapsed_time


class QueueEntryStatus(StrEnum):
    """The lifecycle states supported by the best-effort worker."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"

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
                        QueueEntryStatus.TIMEOUT,
                    }
                )
            case (
                QueueEntryStatus.SUCCEEDED
                | QueueEntryStatus.FAILED
                | QueueEntryStatus.CANCELLED
                | QueueEntryStatus.TIMEOUT
            ):
                return frozenset()


_WIRE_DECODERS: Mapping[str, Callable[[Any], Any]] = {
    "id": uuid.UUID,
    "status": QueueEntryStatus,
    "queued_at": ClockTime.from_timestamp,
    "dispatched_at": ClockTime.from_timestamp,
    "finished_at": ClockTime.from_timestamp,
}


def _encode_wire_value(name: str, value: Any) -> Any:
    """Render a field value in its JSON-compatible durable form."""
    if name not in _WIRE_DECODERS or value is None:
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    return value.to_timestamp()


def _decode_wire_value(name: str, value: Any) -> Any:
    """Restore a field value from its durable form.

    The decoders raise either class depending on how a stored value is wrong --
    `uuid.UUID` and the status enum raise ValueError, `ClockTime.from_timestamp`
    raises TypeError for a non-number and ValueError for one out of range. A
    caller restoring a record should not have to catch both and still be left
    guessing which field was at fault, so both become one error naming it,
    mirroring how `validate_json_value` normalises `json.dumps`.
    """
    decoder = _WIRE_DECODERS.get(name)
    if decoder is None or value is None:
        return value
    try:
        return decoder(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Queue entry {name} could not be restored from {value!r}"
        ) from exc


def validate_budget(value: Any) -> float:
    """Return *value* as an execution budget, or raise if it is not one.

    The single definition of what a budget is, shared by every point one can be
    supplied from -- the entry record, the queue's `TIMEOUT` setting, and the
    worker override -- so the three cannot drift apart.

    A budget is a finite, strictly positive count of seconds. Zero and negative
    values would abandon a handler the moment it started, and infinity is a
    value meaning unbounded, which is the defect a budget exists to remove. NaN
    is neither, and compares false against every bound, so it must be excluded
    by name rather than by comparison.
    """
    # `type(...) is` rather than isinstance: bool is an int, and a flag standing
    # in for a duration is exactly the confusion worth catching here.
    if type(value) not in (int, float):
        raise TypeError(f"Execution budget must be a number of seconds, not {value!r}")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(
            f"Execution budget must be a finite positive number of seconds, not {value!r}"
        )
    return value


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
    queued_at: ClockTime
    dispatched_at: ClockTime | None
    finished_at: ClockTime | None
    payload: Any
    result: Any | None
    error: dict[str, str] | None
    # A duration, not the instant at which the entry expires: named for its
    # unit so it cannot be read as one of the lifecycle instants above.
    timeout_seconds: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, uuid.UUID):
            raise TypeError("Queue entry id must be a UUID")
        if not isinstance(self.queue, str):
            raise TypeError("Queue entry queue must be a string")
        if not isinstance(self.status, QueueEntryStatus):
            raise TypeError("Queue entry status must be a QueueEntryStatus")
        if not isinstance(self.queued_at, ClockTime):
            raise TypeError("Queue entry queued_at must be a ClockTime")
        for name in ("dispatched_at", "finished_at"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, ClockTime):
                raise TypeError(f"Queue entry {name} must be a ClockTime or None")
        if self.timeout_seconds is not None:
            validate_budget(self.timeout_seconds)
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

    @property
    def queued_for(self) -> float | None:
        """Seconds this entry waited before a worker dispatched it.

        Derived rather than stored, so it cannot disagree with the instants it
        comes from. Absent until dispatch, since an entry still waiting has not
        waited zero seconds -- the question has no answer yet.
        """
        return elapsed_time(self.queued_at, self.dispatched_at)

    @property
    def ran_for(self) -> float | None:
        """Seconds this entry's handler ran, once it reached a terminal state."""
        return elapsed_time(self.dispatched_at, self.finished_at)

    @classmethod
    def create(
        cls,
        *,
        queue: str,
        payload: Any,
        queued_at: ClockTime | None = None,
        timeout_seconds: float | None = None,
    ) -> QueueEntry:
        """Create a newly queued entry with a queue-owned UUIDv7 and timestamp."""
        return cls(
            id=uuid.uuid7(),
            queue=queue,
            status=QueueEntryStatus.QUEUED,
            queued_at=queued_at or DEFAULT_CLOCK.now(),
            dispatched_at=None,
            finished_at=None,
            payload=payload,
            result=None,
            error=None,
            timeout_seconds=timeout_seconds,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the complete JSON-compatible durable representation."""
        return {
            field.name: _encode_wire_value(field.name, getattr(self, field.name))
            for field in fields(self)
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> QueueEntry:
        """Rebuild an entry from its JSON-decoded durable representation.

        A record that cannot be restored fails one way whichever way it is
        wrong -- a field missing, a stored value that will not decode, or one
        the record itself rejects -- naming the field and chaining the cause, so
        a caller catches one class and an operator reads one kind of message.
        """
        missing = [
            field.name
            for field in fields(cls)
            if field.init
            and field.name not in value
            and field.default is MISSING
            and field.default_factory is MISSING
        ]
        if missing:
            raise ValueError(
                f"Queue entry record is missing {', '.join(sorted(missing))}"
            )
        decoded = {
            field.name: _decode_wire_value(field.name, value[field.name])
            for field in fields(cls)
            if field.init and field.name in value
        }
        try:
            return cls(**decoded)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Queue entry record is invalid: {exc}") from exc
