try:
    import redis

    from typing import Dict
    import json

    from django_queue.backends.exceptions import QueueEncodingException
    from .redisqueue import RedisQueue


    def _encode(item: Dict) -> str:
        try:
            return json.dumps(item)
        except TypeError as e:
            raise QueueEncodingException from e


    def _decode(item: str) -> Dict:
        try:
            return item if isinstance(item, Dict) else json.loads(item)
        except (json.JSONDecodeError, TypeError) as e:
            raise QueueEncodingException from e


    class RedisQueueJson(RedisQueue):
        def add(self, *items: Dict | str) -> None:
            super().add(*(_encode(item) for item in items if item is not None))

        def get(self) -> Dict | str:
            return _decode(super().get())

        def poll(self) -> Dict | str:
            return _decode(super().poll())

        def peek(self) -> Dict | str:
            return _decode(super().peek())


    class RedisStackJson(RedisQueueJson):
        def __init__(self, redis_spec, options: Dict | None = None, **kwargs):
            options = {} if options is None else options
            options |= kwargs
            options.setdefault("stack", True)
            super().__init__(redis_spec, options)

except ImportError:
    pass
