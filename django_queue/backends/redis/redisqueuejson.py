try:
    import json

    from django_queue.backends.exceptions import QueueEncodingException

    from .redisqueue import RedisQueue


    def _encode(item: dict) -> str:
        try:
            return json.dumps(item)
        except TypeError as e:
            raise QueueEncodingException from e


    def _decode(item: str) -> dict:
        try:
            return item if isinstance(item, dict) else json.loads(item)
        except (json.JSONDecodeError, TypeError) as e:
            raise QueueEncodingException from e


    class RedisQueueJson(RedisQueue):
        def add(self, *items: dict | str) -> None:
            super().add(*(_encode(item) for item in items if item is not None))

        def get(self) -> dict | str:
            return _decode(super().get())

        def poll(self) -> dict | str:
            return _decode(super().poll())

        def peek(self) -> dict | str:
            return _decode(super().peek())


    class RedisStackJson(RedisQueueJson):
        def __init__(self, redis_spec, options: dict | None = None, **kwargs):
            options = {} if options is None else options
            options |= kwargs
            options.setdefault("stack", True)
            super().__init__(redis_spec, options)

except ImportError:
    pass
