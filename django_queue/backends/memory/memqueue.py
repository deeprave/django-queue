import asyncio
import queue
from dataclasses import replace
from uuid import UUID

from django_queue.clock import DEFAULT_CLOCK, QueueClock
from django_queue.entries import QueueEntry, QueueEntryStatus, validate_json_value
from django_queue.observers import publish_snapshot
from django_queue.signals import send_entry_enqueued

from ..base import AsyncQueue
from ..exceptions import QueueEmptyException, QueueFullException


async def apoll_item(item_queue: queue.Queue):
    """Wait for a raw item without leaving a cancellable thread-side get."""
    while True:
        try:
            return item_queue.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.01)


class MemoryQueue(AsyncQueue):
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
        self._initialise_lifecycle_observers()

    @property
    def stack(self):
        return self._stack

    @property
    def capacity(self):
        return self._maxsize

    async def aadd(self, *items):
        for item in items:
            try:
                self._queue.put_nowait(item)
            except queue.Full as e:
                raise QueueFullException from e

    async def aget(self):
        try:
            return self._queue.get_nowait()
        except queue.Empty as e:
            raise QueueEmptyException from e

    async def apoll(self):
        """Wait for a raw item without blocking or losing it on cancellation."""
        return await apoll_item(self._queue)

    async def apeek(self):
        if self._queue.qsize() == 0:
            raise QueueEmptyException
        return self._queue.queue[0]

    async def asize(self):
        return self._queue.qsize()

    async def aclear(self):
        while True:
            try:
                await self.aget()
            except QueueEmptyException:
                break

    async def aenqueue(self, payload, *, timeout_seconds: float | None = None) -> UUID:
        validate_json_value(payload)
        entry = self.entry_class.create(
            queue=self._queue_name,
            payload=payload,
            queued_at=await self.clock.anow(),
            timeout_seconds=timeout_seconds,
        )
        self._entries[entry.id] = entry
        self._pending_entries.put_nowait(entry.id)
        send_entry_enqueued(self, entry=entry)
        return entry.id

    async def aget_entry(self, entry_id: UUID) -> QueueEntry:
        try:
            return self._entries[entry_id]
        except KeyError as exc:
            raise QueueEmptyException from exc

    async def _alist_entries(self) -> list[QueueEntry]:
        return list(self._entries.values())

    async def apublish_lifecycle_snapshot(self, entry: QueueEntry) -> None:
        publish_snapshot(self, entry)

    async def adequeue_entry(self) -> QueueEntry:
        try:
            return await self.aget_entry(self._pending_entries.get_nowait())
        except queue.Empty as exc:
            raise QueueEmptyException from exc

    async def ahas_pending_entries(self) -> bool:
        return not self._pending_entries.empty()

    async def amark_running(self, entry_id: UUID) -> QueueEntry:
        return await self._areplace_entry(
            entry_id,
            status=QueueEntryStatus.RUNNING,
            dispatched_at=await self.clock.anow(),
        )

    async def amark_succeeded(self, entry_id: UUID, result) -> QueueEntry:
        validate_json_value(result)
        return await self._areplace_entry(
            entry_id,
            status=QueueEntryStatus.SUCCEEDED,
            result=result,
            error=None,
            finished_at=await self.clock.anow(),
        )

    async def amark_failed(self, entry_id: UUID, error: Exception) -> QueueEntry:
        return await self._areplace_entry(
            entry_id,
            status=QueueEntryStatus.FAILED,
            error={"type": type(error).__name__, "message": str(error)},
            finished_at=await self.clock.anow(),
        )

    async def amark_cancelled(self, entry_id: UUID) -> QueueEntry:
        return await self._areplace_entry(
            entry_id,
            status=QueueEntryStatus.CANCELLED,
            finished_at=await self.clock.anow(),
        )

    async def amark_timed_out(self, entry_id: UUID) -> QueueEntry:
        return await self._areplace_entry(
            entry_id,
            status=QueueEntryStatus.TIMEOUT,
            finished_at=await self.clock.anow(),
        )

    async def _areplace_entry(
        self, entry_id: UUID, *, status: QueueEntryStatus, **changes
    ) -> QueueEntry:
        if not isinstance(status, QueueEntryStatus):
            raise TypeError("Queue entry status must be a QueueEntryStatus")
        previous_entry = await self.aget_entry(entry_id)
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
