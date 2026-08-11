try:
    import json

    from django_queue.backends.exceptions import QueueEncodingException

    from .redisqueue import RedisQueue

    def _encode(item: dict | str) -> str:
        try:
            return json.dumps(item)
        except TypeError as e:
            raise QueueEncodingException from e

    def _decode(item: dict | str) -> dict | str:
        try:
            return item if isinstance(item, dict) else json.loads(item)
        except (json.JSONDecodeError, TypeError) as e:
            raise QueueEncodingException from e

    class RedisQueueJson(RedisQueue):
        async def aadd(self, *items: dict | str) -> None:
            await super().aadd(*(_encode(item) for item in items if item is not None))

        async def aget(self) -> dict | str:
            return _decode(await super().aget())

        async def apoll(self) -> dict | str:
            return _decode(await super().apoll())

        async def apeek(self) -> dict | str:
            return _decode(await super().apeek())

    class RedisStackJson(RedisQueueJson):
        def __init__(self, redis_spec, options: dict | None = None, **kwargs):
            options = {} if options is None else options
            options |= kwargs
            options.setdefault("stack", True)
            super().__init__(redis_spec, options)

except ImportError:
    pass
