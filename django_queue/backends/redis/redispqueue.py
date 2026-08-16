try:
    from .redisqueue import RedisAsyncQueue

    class RedisAsyncPriorityQueue(RedisAsyncQueue):
        def __init__(self, redis_url: str, options: dict | None = None, **kwargs):
            super().__init__(redis_url, options, **kwargs)

        @property
        def stack(self):
            return False

        def poll(self, timeout: int = 0, retries: int = 10):
            return self._run_synchronously(self.apoll, timeout, retries)

        async def aadd(self, *items):
            await self._provider.aadd_priority(*items)

        async def aget(self):
            return await self._provider.aget_priority()

        async def apoll(self, timeout: int = 0, retries: int = 10):
            """Remove and return the highest-priority item.

            A positive ``timeout`` applies to each blocking attempt. The call
            can therefore wait up to ``timeout * retries`` before raising
            ``QueueEmptyException``; with a positive timeout, zero retries
            means keep trying.
            """
            return await self._provider.apoll_priority(timeout, retries)

        async def apeek(self):
            """
            Retrieve (but don't remove) the highest-priority item from the priority queue.
            :return: The item with the highest priority.
            Raises QueueEmptyException if the queue is empty.
            """
            return await self._provider.apeek_priority()

        async def asize(self) -> int:
            """
            Get the current size of the priority queue.
            :return: Number of items in the queue.
            """
            return await self._provider.asize_priority()

        async def aclear(self) -> None:
            """
            Clear all items in the queue.
            """
            await self._provider.aclear_priority()

except ImportError:
    pass
