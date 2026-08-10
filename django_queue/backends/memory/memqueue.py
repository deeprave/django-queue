import queue
from dataclasses import replace
from uuid import UUID

from django_queue.clock import DEFAULT_CLOCK, QueueClock
from django_queue.entries import QueueEntry, QueueEntryStatus, validate_json_value
from django_queue.signals import send_entry_enqueued

from ..base import BaseQueue
from ..exceptions import QueueEmptyException, QueueFullException


class MemoryQueue(BaseQueue):
    def __init__(self, _: str | None = None, options: dict | None = None, **kwargs):
        options = {} if options is None else options
        options |= kwargs
        self._maxsize = options.pop("maxsize", 0)
        self._stack = bool(options.pop("stack", False))
        self._queue = (queue.LifoQueue if self._stack else queue.Queue)(
            maxsize=self._maxsize
        )
        self._queue_name = options.pop("queue_name", "default")
        self._clock: QueueClock = options.pop("clock", DEFAULT_CLOCK)
        self._entries: dict[UUID, QueueEntry] = {}
        pending_entry_queue = queue.LifoQueue if self._stack else queue.Queue
        self._pending_entries: queue.Queue[UUID] = pending_entry_queue()

    @property
    def stack(self):
        return self._stack

    @property
    def capacity(self):
        return self._maxsize

    def add(self, *items):
        for item in items:
            try:
                self._queue.put_nowait(item)
            except queue.Full as e:
                raise QueueFullException from e

    def get(self):
        try:
            return self._queue.get_nowait()
        except queue.Empty as e:
            raise QueueEmptyException from e

    def poll(self):
        return self._queue.get(block=True)

    def peek(self):
        if self._queue.qsize() == 0:
            raise QueueEmptyException
        return self._queue.queue[0]

    def size(self):
        return self._queue.qsize()

    def clear(self):
        while True:
            try:
                self.get()
            except QueueEmptyException:
                break

    def enqueue(self, payload, *, timeout_seconds: float | None = None) -> UUID:
        validate_json_value(payload)
        entry = self.entry_class.create(
            queue=self._queue_name,
            payload=payload,
            queued_at=self.clock.now(),
            timeout_seconds=timeout_seconds,
        )
        self._entries[entry.id] = entry
        self._pending_entries.put_nowait(entry.id)
        send_entry_enqueued(self, entry=entry)
        return entry.id

    def get_entry(self, entry_id: UUID) -> QueueEntry:
        try:
            return self._entries[entry_id]
        except KeyError as exc:
            raise QueueEmptyException from exc

    def dequeue_entry(self) -> QueueEntry:
        try:
            return self.get_entry(self._pending_entries.get_nowait())
        except queue.Empty as exc:
            raise QueueEmptyException from exc

    def has_pending_entries(self) -> bool:
        return not self._pending_entries.empty()

    def mark_running(self, entry_id: UUID) -> QueueEntry:
        return self._replace_entry(
            entry_id, status=QueueEntryStatus.RUNNING, dispatched_at=self.clock.now()
        )

    def mark_succeeded(self, entry_id: UUID, result) -> QueueEntry:
        validate_json_value(result)
        return self._replace_entry(
            entry_id,
            status=QueueEntryStatus.SUCCEEDED,
            result=result,
            error=None,
            finished_at=self.clock.now(),
        )

    def mark_failed(self, entry_id: UUID, error: Exception) -> QueueEntry:
        return self._replace_entry(
            entry_id,
            status=QueueEntryStatus.FAILED,
            error={"type": type(error).__name__, "message": str(error)},
            finished_at=self.clock.now(),
        )

    def mark_cancelled(self, entry_id: UUID) -> QueueEntry:
        return self._replace_entry(
            entry_id,
            status=QueueEntryStatus.CANCELLED,
            finished_at=self.clock.now(),
        )

    def mark_timed_out(self, entry_id: UUID) -> QueueEntry:
        return self._replace_entry(
            entry_id,
            status=QueueEntryStatus.TIMEOUT,
            finished_at=self.clock.now(),
        )

    def _replace_entry(
        self, entry_id: UUID, *, status: QueueEntryStatus, **changes
    ) -> QueueEntry:
        if not isinstance(status, QueueEntryStatus):
            raise TypeError("Queue entry status must be a QueueEntryStatus")
        previous_entry = self.get_entry(entry_id)
        if status not in previous_entry.status.next_state():
            raise ValueError(
                f"Cannot transition queue entry from {previous_entry.status} to {status}"
            )
        entry = replace(previous_entry, status=status, **changes)
        self._entries[entry_id] = entry
        return entry


class MemoryStack(MemoryQueue):
    def __init__(self, _: str | None = None, options: dict | None = None, **kwargs):
        options = {} if options is None else options
        options |= kwargs
        options.setdefault("stack", True)
        super().__init__(_, options)
