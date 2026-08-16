try:
    import logging

    from django_queue.backends.base import AsyncQueue
    from django_queue.entries import QueueEntry

    from .provider import QueueProviderRedis

    logger = logging.getLogger(__name__)

    class RedisAsyncQueue(AsyncQueue):
        recovery_batch_size = 100
        requires_entry_class_at_construction = True
        worker_provider_kind = "redis"
        worker_provider_type = "redis"
        worker_class = "django_queue.backends.redis.RedisAsyncQueueWorker"
        compatible_worker_class = "django_queue.backends.redis.RedisAsyncQueueWorker"

        def __init__(self, redis_url: str, options: dict | None = None, **kwargs):
            options = {} if options is None else options
            options |= kwargs
            self.entry_class = options.pop("entry_class", self.entry_class)
            self._provider = QueueProviderRedis(
                redis_url, options, entry_class=self.entry_class
            )
            self._queue_name = self._provider.queue_name
            self._clock = self._provider.clock
            self._initialise_observers()

        async def apublish(self, entry: QueueEntry) -> None:
            try:
                await self._provider.apublish(entry)
            except Exception:
                logger.exception(
                    "Unable to publish queue lifecycle snapshot for entry %s", entry.id
                )

        def _observer_receiver(self, on_snapshot):
            self._configure_provider_entry_class()
            return lambda: self._provider.observe(on_snapshot)

        async def aclose(self) -> None:
            await self._provider.aclose()

    class RedisAsyncStack(RedisAsyncQueue):
        def __init__(self, redis_url: str, options: dict | None = None, **kwargs):
            options = {} if options is None else options
            options |= kwargs
            options.setdefault("stack", True)
            super().__init__(redis_url, options)

except ImportError:
    pass
