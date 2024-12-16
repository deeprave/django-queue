try:
    import redis

    from typing import Union, Dict, Tuple, AnyStr

    from django_queue.backends.exceptions import QueueFullException, QueueEmptyException
    from .redisqueue import RedisQueue, _encode, _decode


    class RedisPriorityQueue(RedisQueue):
        def __init__(self, redis_spec: Union[str, redis.Redis], options: Dict = None, **kwargs):
            super().__init__(redis_spec, options, **kwargs)

        @property
        def stack(self):
            return False

        def add(self, *items: Tuple[int, AnyStr]):
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
                self._redis.zadd(self._queue_name, {_encode(value, self._encoding): priority}, nx=True)

        def get(self) -> str:
            """
            Get and remove the next item from the priority queue.
            :return: The item with the lowest priority.
            Raises QueueEmptyException if the queue is empty.
            """
            # Retrieve the lowest-priority item
            if item := self._redis.zrevrange(self._queue_name, 0, 0, withscores=False):
                self._redis.zrem(self._queue_name, item[0])
                return _decode(item[0], self._encoding)
            raise QueueEmptyException

        def poll(self, timeout: int = 0, retries: int = 10) -> str:
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
                except QueueEmptyException as e:
                    if timeout <= 0:
                        raise e
                    if item := self._redis.blpop(self._queue_name, timeout=timeout):
                        return _decode(item[1], self._encoding)
            raise QueueEmptyException

        def peek(self) -> str:
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
