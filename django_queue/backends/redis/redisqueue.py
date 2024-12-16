try:
    import redis

    import uuid
    from typing import AnyStr, Dict

    from django_queue.backends.exceptions import QueueFullException, QueueEmptyException, QueueEncodingException
    from django_queue.backends.base import BaseQueue


    def _encode(item: str, encoding: str) -> bytes:
        try:
            return item.encode(encoding)
        except UnicodeEncodeError as e:
            raise QueueEncodingException from e


    def _decode(item: AnyStr, encoding: str) -> str:
        try:
            return item.decode(encoding)
        except UnicodeDecodeError as e:
            raise QueueEncodingException from e


    def random_queue_name() -> str:
        return f"queue_{uuid.uuid4().hex}"


    class RedisQueue(BaseQueue):
        def __init__(self, redis_spec, options: Dict | None = None, **kwargs):
            self._redis = redis.from_url(redis_spec) if isinstance(redis_spec, str) else redis_spec
            options = {} if options is None else options
            options |= kwargs
            self._queue_name = options.get("queue_name", random_queue_name())
            self._stack = bool(options.pop("stack", False))
            self._maxsize = options.get("maxsize", 0)
            self._encoding = options.get("encoding", "utf-8")
            # Determine operation based on self._stack
            self.push = self._redis.rpush if self._stack else self._redis.lpush
            self.pop = self._redis.rpop if self._stack else self._redis.lpop
            self.bpop = self._redis.brpop if self._stack else self._redis.blpop

        @property
        def stack(self):
            return self._stack

        def capacity(self):
            return self._maxsize

        def add(self, *items: str):
            if items:
                current_size = self.size()
                if self._maxsize != 0 and current_size + len(items) > self._maxsize:
                    raise QueueFullException
                self.push(self._queue_name, *(_encode(item, self._encoding) for item in items if item is not None))

        def get(self) -> str:
            if self.size() == 0:
                raise QueueEmptyException
            return _decode(self.pop(self._queue_name), self._encoding)

        def poll(self) -> str:
            return _decode(self.bpop([self._queue_name], 0)[1], self._encoding)

        def peek(self):
            if self.size() == 0:
                raise QueueEmptyException
            if self._stack:  # LIFO: Peek last (rightmost) item
                return _decode(self._redis.lrange(self._queue_name, -1, -1)[0], self._encoding)
            else:  # FIFO: Peek first (leftmost) item
                return _decode(self._redis.lrange(self._queue_name, 0, 0)[0], self._encoding)

        def size(self):
            return self._redis.llen(self._queue_name)

        def clear(self):
            self._redis.delete(self._queue_name)


    class RedisStack(RedisQueue):
        def __init__(self, redis_spec, options: Dict | None = None, **kwargs):
            options = {} if options is None else options
            options |= kwargs
            options.setdefault("stack", True)
            super().__init__(redis_spec, options)

except ImportError:
    pass