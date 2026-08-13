import queue

from django_queue.clock import DEFAULT_CLOCK

from ..base import AsyncQueue
from ..exceptions import QueueEmptyException, QueueFullException
from .memqueue import MemoryQueue, apoll_item


class MemoryPriorityQueue(AsyncQueue):
    def __init__(self, _: str | None = None, options: dict | None = None, **kwargs):
        options = {} if options is None else options
        options |= kwargs
        self._maxsize = options.pop("maxsize", 0)
        self._queue: queue.PriorityQueue = queue.PriorityQueue(maxsize=self._maxsize)
        self._queue_name = options.pop("queue_name", "default")
        self._clock = options.pop("clock", DEFAULT_CLOCK)
        self._entries = {}
        self._pending_entries = queue.Queue()
        self._initialise_lifecycle_observers()

    @property
    def capacity(self):
        return self._maxsize

    async def aadd(self, *items):
        for item in items:
            priority, value = 0, item
            if isinstance(value, (tuple, list)):
                priority, *value = item
                value = value[0] if len(value) == 1 else tuple(value)
            if self._queue.full():
                raise QueueFullException
            self._queue.put_nowait((-int(priority), value))

    async def aget(self):
        if self._queue.empty():
            raise QueueEmptyException
        return self._queue.get_nowait()[1]

    async def apoll(self):
        return (await apoll_item(self._queue))[1]

    async def apeek(self):
        if self._queue.empty():
            raise QueueEmptyException
        return self._queue.queue[0][1]

    async def asize(self):
        return self._queue.qsize()

    async def aclear(self):
        while True:
            try:
                await self.aget()
            except QueueEmptyException:
                break

    aenqueue = MemoryQueue.aenqueue
    aget_entry = MemoryQueue.aget_entry
    _alist_entries = MemoryQueue._alist_entries
    apublish_lifecycle_snapshot = MemoryQueue.apublish_lifecycle_snapshot
    adequeue_entry = MemoryQueue.adequeue_entry
    ahas_pending_entries = MemoryQueue.ahas_pending_entries
    amark_running = MemoryQueue.amark_running
    amark_succeeded = MemoryQueue.amark_succeeded
    amark_failed = MemoryQueue.amark_failed
    amark_cancelled = MemoryQueue.amark_cancelled
    amark_timed_out = MemoryQueue.amark_timed_out
    _areplace_entry = MemoryQueue._areplace_entry
