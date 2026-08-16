try:
    import redis  # noqa: F401 - verifies the optional Redis backend dependency.
except ModuleNotFoundError as exc:
    raise ImportError(
        "Redis queue backends require the 'redis' extra; install django-queue[redis]"
    ) from exc

from .rediseventqueue import RedisEventQueue
from .redispqueue import RedisAsyncPriorityQueue
from .redispqueuejson import RedisAsyncPriorityQueueJson
from .redisqueue import RedisAsyncQueue, RedisAsyncStack
from .redisqueuejson import RedisAsyncQueueJson, RedisAsyncStackJson
from .worker import RedisAsyncQueueWorker, RedisEventQueueWorker

__all__ = (
    "RedisAsyncPriorityQueue",
    "RedisAsyncPriorityQueueJson",
    "RedisAsyncQueue",
    "RedisAsyncQueueJson",
    "RedisAsyncQueueWorker",
    "RedisAsyncStack",
    "RedisAsyncStackJson",
    "RedisEventQueue",
    "RedisEventQueueWorker",
)
