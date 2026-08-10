import queue

from django_queue.clock import DEFAULT_CLOCK

from ..base import BaseQueue
from ..exceptions import QueueEmptyException, QueueFullException
from .memqueue import MemoryQueue


class MemoryPriorityQueue(BaseQueue):
    def __init__(self, _: str | None = None, options: dict | None = None, **kwargs):
        options = {} if options is None else options
        options |= kwargs
        self._maxsize = options.pop("maxsize", 0)
        self._queue: queue.PriorityQueue = queue.PriorityQueue(maxsize=self._maxsize)
        self._queue_name = options.pop("queue_name", "default")
        self._clock = options.pop("clock", DEFAULT_CLOCK)
        self._entries = {}
        self._pending_entries = queue.Queue()

    @property
    def capacity(self):
        return self._maxsize

    def add(self, *items):
        for item in items:
            priority, value = 0, item
            if isinstance(value, (tuple, list)):
                priority, *value = item
                value = value[0] if len(value) == 1 else tuple(value)
            if self._queue.full():
                raise QueueFullException
            self._queue.put_nowait((-int(priority), value))

    def get(self):
        if self._queue.empty():
            raise QueueEmptyException
        return self._queue.get_nowait()[1]

    def poll(self):
        return self._queue.get(block=True)[1]

    def peek(self):
        if self._queue.empty():
            raise QueueEmptyException
        return self._queue.queue[0][1]

    def size(self):
        return self._queue.qsize()

    def clear(self):
        while True:
            try:
                self.get()
            except QueueEmptyException:
                break

    enqueue = MemoryQueue.enqueue
    get_entry = MemoryQueue.get_entry
    dequeue_entry = MemoryQueue.dequeue_entry
    has_pending_entries = MemoryQueue.has_pending_entries
    mark_running = MemoryQueue.mark_running
    mark_succeeded = MemoryQueue.mark_succeeded
    mark_failed = MemoryQueue.mark_failed
    mark_cancelled = MemoryQueue.mark_cancelled
    mark_timed_out = MemoryQueue.mark_timed_out
    _replace_entry = MemoryQueue._replace_entry
