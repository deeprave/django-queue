try:
    import redis

    from typing import Dict, Tuple

    from .redispqueue import RedisPriorityQueue
    from .redisqueuejson import _encode, _decode


    class RedisPriorityQueueJson(RedisPriorityQueue):
        def add(self, *items: Tuple[int, Dict | str]) -> None:
            super().add(*((priority, _encode(item)) for priority, item in items if item is not None))

        def get(self) -> Dict | str:
            return _decode(super().get())

        def poll(self, timeout: int = 0, retries: int = 10) -> Dict | str:
            fetched = super().poll(timeout=timeout)
            return _decode(fetched)

        def peek(self) -> Dict | str:
            return _decode(super().peek())

except ImportError:
    pass
