from django_queue.clock import DEFAULT_CLOCK

from ..base import AsyncQueue
from .memqueue import MemoryAsyncQueue
from .provider import QueueProviderMemory


class MemoryAsyncPriorityQueue(AsyncQueue):
    worker_class = "django_queue.backends.memory.MemoryAsyncQueueWorker"

    def __init__(self, _: str | None = None, options: dict | None = None, **kwargs):
        options = {} if options is None else options
        options |= kwargs
        self.entry_class = options.pop("entry_class", self.entry_class)
        maxsize = options.pop("maxsize", 0)
        self._queue_name = options.pop("queue_name", "default")
        self._clock = options.pop("clock", DEFAULT_CLOCK)
        self._provider = QueueProviderMemory(
            clock=self._clock,
            maxsize=maxsize,
        )
        self._initialise_lifecycle_observers()

    async def aadd(self, *items):
        await self._provider.aadd_priority_items(*items)

    async def aget(self):
        return await self._provider.aget_priority_item()

    async def apoll(self):
        return await self._provider.apoll_priority_item()

    async def apeek(self):
        return await self._provider.apeek_priority_item()

    async def asize(self):
        return await self._provider.asize_priority_items()

    async def aclear(self):
        await self._provider.aclear_priority_items()

    aenqueue = MemoryAsyncQueue.aenqueue
    aget_entry = MemoryAsyncQueue.aget_entry
    aprune_entry = MemoryAsyncQueue.aprune_entry
    _aprune_expired_entries = MemoryAsyncQueue._aprune_expired_entries
    apublish_lifecycle_snapshot = MemoryAsyncQueue.apublish_lifecycle_snapshot
    adequeue_entry = MemoryAsyncQueue.adequeue_entry
    ahas_pending_entries = MemoryAsyncQueue.ahas_pending_entries
    _amark_running = MemoryAsyncQueue._amark_running
    _amark_succeeded = MemoryAsyncQueue._amark_succeeded
    _amark_failed = MemoryAsyncQueue._amark_failed
    _amark_cancelled = MemoryAsyncQueue._amark_cancelled
    _amark_timed_out = MemoryAsyncQueue._amark_timed_out
    _areplace_entry = MemoryAsyncQueue._areplace_entry
