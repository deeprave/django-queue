try:
    import redis

    from django_queue.backends.exceptions import (
        QueueEmptyException,
        QueueEncodingException,
        QueueFullException,
    )

    from .redisqueue import RedisQueue, _decode, _encode

    class RedisPriorityQueue(RedisQueue):
        def __init__(
            self, redis_spec: str | redis.Redis, options: dict | None = None, **kwargs
        ):
            super().__init__(redis_spec, options, **kwargs)

        @property
        def stack(self):
            return False

        def add(self, *items):
            """
            Add one or more priority, item pairs to the priority queue.
            :param items:
                priority: Priority of the items e.g. (-100 = lowest, +100 = highest).
                item: The item or message to insert.
            Raises QueueFullException if the queue exceeds the size limit.
            """
            for item in items:
                priority, value = item
                if self._maxsize != 0 and self.size() >= self._maxsize:
                    raise QueueFullException
                self._redis.zadd(
                    self._queue_name,
                    {_encode(value, self._encoding): priority},
                    nx=True,
                )

        def get(self):
            """
            Get and remove the next item from the priority queue.
            :return: The item with the highest priority.
            Raises QueueEmptyException if the queue is empty.
            """
            # Retrieve the highest-priority item
            if item := self._redis.zrevrange(self._queue_name, 0, 0, withscores=False):
                # Without withscores the reply is a flat list of members, but
                # redis-py's stubs do not overload on that, so narrow before
                # handing the member back to Redis. The accepted types match
                # `_decode` and zrem's own signature. With a bytes client, pass
                # the member rather than a decoded copy: it must match what was
                # stored, and self._encoding need not be the connection's.
                # `peek` needs no guard, since `_decode` accepts object.
                member = item[0]
                if not isinstance(member, bytes | bytearray | memoryview | str):
                    raise QueueEncodingException(
                        f"Queue value must be text or bytes, not {type(member).__name__}"
                    )
                self._redis.zrem(self._queue_name, member)
                return _decode(member, self._encoding)
            raise QueueEmptyException

        def poll(self, timeout: int = 0, retries: int = 10):
            """
            Retrieves the next item from the queue without removing it (peek).
            :return: The item with the lowest priority.
            Raises QueueEmptyException if the queue is empty.
            """
            # sourcery skip: use-assigned-variable
            attempt = retries
            while retries == 0 or attempt > 0:
                attempt -= 1
                try:  # Attempt to get the highest-priority item normally
                    return self.get()
                except QueueEmptyException:
                    if timeout <= 0:
                        raise
                    if item := self._redis.blpop(self._queue_name, timeout=timeout):
                        return _decode(item[1], self._encoding)
            raise QueueEmptyException

        def peek(self):
            """
            Retrieve (but don't remove) the highest-priority item from the priority queue.
            :return: The item with the highest priority.
            Raises QueueEmptyException if the queue is empty.
            """
            if item := self._redis.zrevrange(self._queue_name, 0, 0, withscores=False):
                return _decode(item[0], self._encoding)
            raise QueueEmptyException

        def size(self) -> int:
            """
            Get the current size of the priority queue.
            :return: Number of items in the queue.
            """
            return self._redis.zcard(self._queue_name)

        def clear(self) -> None:
            """
            Clear all items in the queue.
            """
            self._redis.delete(self._queue_name)

except ImportError:
    pass
