import queue
from typing import Dict

from ..exceptions import QueueFullException, QueueEmptyException
from ..base import BaseQueue


class MemoryQueue(BaseQueue):
    def __init__(self, _: str | None = None, options: Dict | None = None, **kwargs):
        options = {} if options is None else options
        options |= kwargs
        self._maxsize = options.pop("maxsize", 0)
        self._stack = bool(options.pop("stack", False))
        self._queue = (queue.LifoQueue if self._stack else queue.Queue)(maxsize=self._maxsize)

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


class MemoryStack(MemoryQueue):
    def __init__(self, _: str | None = None, options: Dict | None = None, **kwargs):
        options = {} if options is None else options
        options |= kwargs
        options.setdefault("stack", True)
        super().__init__(_, options)
