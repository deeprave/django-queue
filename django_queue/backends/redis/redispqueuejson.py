try:
    from .redispqueue import RedisPriorityQueue
    from .redisqueuejson import _decode, _encode

    class RedisPriorityQueueJson(RedisPriorityQueue):
        def add(self, *items: tuple[int, dict | str]) -> None:
            super().add(
                *(
                    (priority, _encode(item))
                    for priority, item in items
                    if item is not None
                )
            )

        def get(self) -> dict | str:
            return _decode(super().get())

        def poll(self, timeout: int = 0, retries: int = 10) -> dict | str:
            fetched = super().poll(timeout=timeout)
            return _decode(fetched)

        def peek(self) -> dict | str:
            return _decode(super().peek())

except ImportError:
    pass
