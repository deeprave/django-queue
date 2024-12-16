import queue
from typing import Dict

from ..exceptions import QueueFullException, QueueEmptyException, QueueValueError
from ..base import BaseQueue


class MemoryPriorityQueue(BaseQueue):
    def __init__(self, _: str | None = None, options: Dict | None = None, **kwargs):
        options = {} if options is None else options
        options |= kwargs
        self._maxsize = options.pop("maxsize", 0)
        self._queue: queue.PriorityQueue = queue.PriorityQueue(maxsize=self._maxsize)

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
