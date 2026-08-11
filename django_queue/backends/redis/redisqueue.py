try:
    import asyncio
    import inspect
    import json
    import uuid
    from dataclasses import replace

    import redis
    import redis.asyncio as async_redis
    from asgiref.sync import async_to_sync
    from redis.asyncio.retry import Retry as AsyncRetry
    from redis.retry import Retry

    from django_queue.backends.base import BaseQueue
    from django_queue.backends.exceptions import (
        InvalidQueueBackendError,
        QueueEmptyException,
        QueueEncodingException,
        QueueFullException,
    )
    from django_queue.clock import QueueClockError, RedisQueueClock
    from django_queue.entries import QueueEntry, QueueEntryStatus, validate_json_value
    from django_queue.signals import send_entry_enqueued

    _ASYNC_REDIS_ARGUMENTS = frozenset(
        inspect.signature(async_redis.Redis.__init__).parameters
    ) - {"self"}

    def _encode(item: str, encoding: str) -> bytes:
        try:
            return item.encode(encoding)
        except UnicodeEncodeError as e:
            raise QueueEncodingException from e

    # Accepts `object` rather than a narrower type because redis-py's return
    # types depend on its client's decode_responses setting, which it cannot
    # express statically. Unlike `Any` this asserts nothing: every branch below
    # is narrowed and checked.
    def _decode(item: object, encoding: str) -> str:
        if isinstance(item, str):
            return item
        if isinstance(item, bytes | bytearray | memoryview):
            try:
                return bytes(item).decode(encoding)
            except UnicodeDecodeError as e:
                raise QueueEncodingException from e
        raise QueueEncodingException(
            f"Queue value must be text or bytes, not {type(item).__name__}"
        )

    def random_queue_name() -> str:
        return f"queue_{uuid.uuid4().hex}"

    class _QueueClockFacade:
        """Expose a queue's loop-local Redis clocks through one clock object."""

        def __init__(self, queue) -> None:
            self._queue = queue

        def now(self):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None and (clock := self._queue._clocks_by_loop.get(loop)):
                return clock.now()
            if loop is not None:
                raise QueueClockError(
                    "Redis queue clock is not calibrated; await queue.clock.anow() first"
                )
            return async_to_sync(self._anow_and_close)()

        async def anow(self):
            return await self._queue._async_clock().anow()

        async def _anow_and_close(self):
            try:
                return await self.anow()
            finally:
                await self._queue.aclose()

    class RedisQueue(BaseQueue):
        def __init__(self, redis_spec, options: dict | None = None, **kwargs):
            self._redis = None if isinstance(redis_spec, str) else redis_spec
            self._redis_spec = redis_spec
            self._async_redis_by_loop = {}
            options = {} if options is None else options
            options |= kwargs
            self._queue_name = options.get("queue_name", random_queue_name())
            self._entry_pending_name = f"{self._queue_name}:entries:pending"
            self._stack = bool(options.pop("stack", False))
            self._maxsize = options.get("maxsize", 0)
            self._encoding = options.get("encoding", "utf-8")
            self._clocks_by_loop = {}
            self._clock = _QueueClockFacade(self)

        def _async_redis(self):
            loop = asyncio.get_running_loop()
            if client := self._async_redis_by_loop.get(loop):
                return client
            if isinstance(self._redis_spec, str):
                client = async_redis.from_url(self._redis_spec)
            else:
                assert self._redis is not None
                connection_kwargs = dict(self._redis.connection_pool.connection_kwargs)
                max_connections = self._redis.connection_pool.max_connections
                connection_class = self._redis.connection_pool.connection_class
                supported_connection_classes = (
                    redis.connection.Connection,
                    redis.connection.SSLConnection,
                    redis.connection.UnixDomainSocketConnection,
                )
                if connection_class not in supported_connection_classes:
                    raise InvalidQueueBackendError(
                        "A supplied Redis client must use Redis, SSL, or Unix socket connections; "
                        "use a Redis URL for custom connection classes"
                    )
                unsupported_tls_options = {
                    name
                    for name in (
                        "ssl_validate_ocsp",
                        "ssl_validate_ocsp_stapled",
                        "ssl_ocsp_context",
                        "ssl_ocsp_expected_cert",
                    )
                    if connection_kwargs.get(name)
                }
                if unsupported_tls_options:
                    raise InvalidQueueBackendError(
                        "A supplied Redis client uses unsupported async TLS options: "
                        f"{', '.join(sorted(unsupported_tls_options))}"
                    )
                async_kwargs = {
                    name: value
                    for name, value in connection_kwargs.items()
                    if name in _ASYNC_REDIS_ARGUMENTS
                }
                retry = async_kwargs.get("retry")
                if isinstance(retry, Retry):
                    # redis-py exposes no public accessors for Retry's policy.
                    # Keep a version change from surfacing as an unhelpful
                    # AttributeError while cloning a configured queue client.
                    try:
                        async_kwargs["retry"] = AsyncRetry(
                            retry._backoff, retry._retries, retry._supported_errors
                        )
                    except AttributeError as exc:
                        raise InvalidQueueBackendError(
                            "A supplied Redis client's retry policy is unsupported"
                        ) from exc
                unsupported_async_options = {
                    name
                    for name in ("redis_connect_func", "credential_provider")
                    if async_kwargs.get(name) is not None
                }
                if unsupported_async_options:
                    raise InvalidQueueBackendError(
                        "A supplied Redis client uses unsupported asynchronous options: "
                        f"{', '.join(sorted(unsupported_async_options))}"
                    )
                async_kwargs["max_connections"] = max_connections
                if path := connection_kwargs.get("path"):
                    pool = async_redis.ConnectionPool(
                        connection_class=async_redis.UnixDomainSocketConnection,
                        path=path,
                        **async_kwargs,
                    )
                    client = async_redis.Redis(connection_pool=pool)
                else:
                    if connection_class is redis.connection.SSLConnection:
                        async_kwargs["ssl"] = True
                    client = async_redis.Redis(**async_kwargs)
            self._async_redis_by_loop[loop] = client
            return client

        def _async_clock(self):
            loop = asyncio.get_running_loop()
            if clock := self._clocks_by_loop.get(loop):
                return clock
            clock = RedisQueueClock(self._async_redis(), asynchronous=True)
            self._clocks_by_loop[loop] = clock
            return clock

        @property
        def stack(self):
            return self._stack

        @property
        def capacity(self):
            return self._maxsize

        # The raw-item API is heterogeneous across this family: JSON variants
        # exchange dicts and priority variants (priority, item) tuples. Leaf
        # classes carry the precise annotations.
        async def aadd(self, *items):
            if items:
                current_size = await self.asize()
                if self._maxsize != 0 and current_size + len(items) > self._maxsize:
                    raise QueueFullException
                await self._async_redis().rpush(
                    self._queue_name,
                    *(
                        _encode(item, self._encoding)
                        for item in items
                        if item is not None
                    ),
                )

        async def aget(self):
            # The size check and the pop are not atomic, so a competing consumer
            # can empty the queue in between; treat that as empty rather than
            # letting None reach the decoder.
            if await self.asize() == 0:
                raise QueueEmptyException
            item = await (
                self._async_redis().rpop(self._queue_name)
                if self._stack
                else self._async_redis().lpop(self._queue_name)
            )
            if item is None:
                raise QueueEmptyException
            return _decode(item, self._encoding)

        async def apoll(self):
            # A zero timeout blocks indefinitely, so this should not return
            # empty-handed; guard rather than subscript a possible None.
            item = await (
                self._async_redis().brpop([self._queue_name], 0)
                if self._stack
                else self._async_redis().blpop([self._queue_name], 0)
            )
            if not item:
                raise QueueEmptyException
            return _decode(item[1], self._encoding)

        async def apeek(self):
            if await self.asize() == 0:
                raise QueueEmptyException
            if self._stack:  # LIFO: Peek last (rightmost) item
                items = await self._async_redis().lrange(self._queue_name, -1, -1)
            else:  # FIFO: Peek first (leftmost) item
                items = await self._async_redis().lrange(self._queue_name, 0, 0)
            if not items:
                raise QueueEmptyException
            return _decode(items[0], self._encoding)

        async def asize(self):
            return await self._async_redis().llen(self._queue_name)

        async def aclear(self):
            await self._async_redis().delete(self._queue_name)

        async def aenqueue(
            self, payload, *, timeout_seconds: float | None = None
        ) -> uuid.UUID:
            validate_json_value(payload)
            entry = self.entry_class.create(
                queue=self._queue_name,
                payload=payload,
                queued_at=await self.clock.anow(),
                timeout_seconds=timeout_seconds,
            )
            await self._astore_entry(entry)
            await self._async_redis().rpush(
                self._entry_pending_name, _encode(str(entry.id), self._encoding)
            )
            send_entry_enqueued(self, entry=entry)
            return entry.id

        async def aget_entry(self, entry_id: uuid.UUID) -> QueueEntry:
            raw_entry = await self._async_redis().get(self._entry_key(entry_id))
            if raw_entry is None:
                raise QueueEmptyException
            return self.entry_class.from_dict(json.loads(raw_entry))

        async def adequeue_entry(self) -> QueueEntry:
            raw_entry_id = await (
                self._async_redis().rpop(self._entry_pending_name)
                if self._stack
                else self._async_redis().lpop(self._entry_pending_name)
            )
            if raw_entry_id is None:
                raise QueueEmptyException
            return await self.aget_entry(
                uuid.UUID(_decode(raw_entry_id, self._encoding))
            )

        async def ahas_pending_entries(self) -> bool:
            return bool(await self._async_redis().llen(self._entry_pending_name))

        async def amark_running(self, entry_id: uuid.UUID) -> QueueEntry:
            return await self._areplace_entry(
                entry_id,
                status=QueueEntryStatus.RUNNING,
                dispatched_at=await self.clock.anow(),
            )

        async def amark_succeeded(self, entry_id: uuid.UUID, result) -> QueueEntry:
            validate_json_value(result)
            return await self._areplace_entry(
                entry_id,
                status=QueueEntryStatus.SUCCEEDED,
                result=result,
                error=None,
                finished_at=await self.clock.anow(),
            )

        async def amark_failed(
            self, entry_id: uuid.UUID, error: Exception
        ) -> QueueEntry:
            return await self._areplace_entry(
                entry_id,
                status=QueueEntryStatus.FAILED,
                error={"type": type(error).__name__, "message": str(error)},
                finished_at=await self.clock.anow(),
            )

        async def amark_cancelled(self, entry_id: uuid.UUID) -> QueueEntry:
            return await self._areplace_entry(
                entry_id,
                status=QueueEntryStatus.CANCELLED,
                finished_at=await self.clock.anow(),
            )

        async def amark_timed_out(self, entry_id: uuid.UUID) -> QueueEntry:
            return await self._areplace_entry(
                entry_id,
                status=QueueEntryStatus.TIMEOUT,
                finished_at=await self.clock.anow(),
            )

        def _entry_key(self, entry_id: uuid.UUID) -> str:
            return f"{self._queue_name}:entries:{entry_id}"

        async def _astore_entry(self, entry: QueueEntry) -> None:
            await self._async_redis().set(
                self._entry_key(entry.id), json.dumps(entry.to_dict())
            )

        async def _areplace_entry(
            self, entry_id: uuid.UUID, *, status: QueueEntryStatus, **changes
        ) -> QueueEntry:
            if not isinstance(status, QueueEntryStatus):
                raise TypeError("Queue entry status must be a QueueEntryStatus")
            previous_entry = await self.aget_entry(entry_id)
            if status not in previous_entry.status.next_state():
                raise ValueError(
                    f"Cannot transition queue entry from {previous_entry.status} to {status}"
                )
            entry = replace(previous_entry, status=status, **changes)
            await self._astore_entry(entry)
            return entry

        async def aclose(self) -> None:
            loop = asyncio.get_running_loop()
            if clock := self._clocks_by_loop.pop(loop, None):
                await clock.aclose()
            if client := self._async_redis_by_loop.pop(loop, None):
                await client.aclose(close_connection_pool=True)

    class RedisStack(RedisQueue):
        def __init__(self, redis_spec, options: dict | None = None, **kwargs):
            options = {} if options is None else options
            options |= kwargs
            options.setdefault("stack", True)
            super().__init__(redis_spec, options)

except ImportError:
    pass
