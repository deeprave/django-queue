try:
    from .redispqueue import RedisPriorityQueue
    from .redisqueuejson import _decode, _encode

    class RedisPriorityQueueJson(RedisPriorityQueue):
        async def aadd(self, *items: tuple[int, dict | str]) -> None:
            await super().aadd(
                *(
                    (priority, _encode(item))
                    for priority, item in items
                    if item is not None
                )
            )

        async def aget(self) -> dict | str:
            return _decode(await super().aget())

        async def apoll(self, timeout: int = 0, retries: int = 10) -> dict | str:
            fetched = await super().apoll(timeout=timeout, retries=retries)
            return _decode(fetched)

        async def apeek(self) -> dict | str:
            return _decode(await super().apeek())

except ImportError:
    pass
