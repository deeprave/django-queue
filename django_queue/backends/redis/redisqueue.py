try:
    import asyncio
    import codecs
    import inspect
    import json
    import logging
    import uuid
    from dataclasses import dataclass, replace

    import redis
    import redis.asyncio as async_redis
    from asgiref.sync import async_to_sync
    from redis.asyncio.retry import Retry as AsyncRetry
    from redis.retry import Retry

    from django_queue.backends.base import BaseQueue
    from django_queue.backends.exceptions import (
        InvalidQueueBackendError,
        QueueClaimConflictError,
        QueueEmptyException,
        QueueEncodingException,
        QueueEntryMissingError,
        QueueFullException,
        QueueValueError,
    )
    from django_queue.clock import (
        MICROSECONDS_PER_SECOND,
        QueueClockError,
        RedisQueueClock,
    )
    from django_queue.entries import (
        QueueEntry,
        QueueEntryStatus,
        validate_budget,
        validate_json_value,
    )
    from django_queue.signals import send_entry_enqueued

    _ASYNC_REDIS_ARGUMENTS = frozenset(
        inspect.signature(async_redis.Redis.__init__).parameters
    ) - {"self"}
    logger = logging.getLogger(__name__)

    _CLAIM_ENTRY_SCRIPT = b"""
        local entry_id
        if ARGV[3] == "1" then
            entry_id = redis.call("RPOP", KEYS[1])
        else
            entry_id = redis.call("LPOP", KEYS[1])
        end
        if not entry_id then
            return {"empty", ""}
        end

        local claim_key = KEYS[2] .. entry_id
        local claimed_at = redis.call("TIME")
        local deadline = tonumber(claimed_at[1]) * 1000000 + tonumber(claimed_at[2]) + tonumber(ARGV[2])
        local claim = cjson.encode({
            worker_id = ARGV[1],
            claimed_at = {
                seconds = tonumber(claimed_at[1]),
                microseconds = tonumber(claimed_at[2])
            },
            lease_deadline = deadline
        })
        if redis.call("SET", claim_key, claim, "NX") then
            redis.call("ZADD", KEYS[3], deadline, entry_id)
            return {"claimed", entry_id}
        end
        if ARGV[3] == "1" then
            redis.call("RPUSH", KEYS[1], entry_id)
        else
            redis.call("LPUSH", KEYS[1], entry_id)
        end
        return {"conflict", entry_id}
    """

    _ACKNOWLEDGE_CLAIM_SCRIPT = b"""
        local raw_claim = redis.call("GET", KEYS[1])
        if not raw_claim then
            return 0
        end
        local decoded, claim = pcall(cjson.decode, raw_claim)
        if not decoded or type(claim) ~= "table" or claim.worker_id ~= ARGV[1] then
            return 0
        end
        redis.call("ZREM", KEYS[2], ARGV[2])
        return redis.call("DEL", KEYS[1])
    """

    _RENEW_CLAIM_SCRIPT = b"""
        local raw_claim = redis.call("GET", KEYS[1])
        if not raw_claim then
            return 0
        end
        local decoded, claim = pcall(cjson.decode, raw_claim)
        if not decoded or type(claim) ~= "table" or claim.worker_id ~= ARGV[1] then
            return 0
        end
        local now = redis.call("TIME")
        local deadline = tonumber(now[1]) * 1000000 + tonumber(now[2]) + tonumber(ARGV[2])
        claim.lease_deadline = deadline
        redis.call("SET", KEYS[1], cjson.encode(claim))
        redis.call("ZADD", KEYS[2], deadline, ARGV[3])
        return 1
    """

    _MARK_CLAIM_RUNNING_SCRIPT = b"""
        local raw_claim = redis.call("GET", KEYS[1])
        if not raw_claim then
            return {0, ""}
        end
        local decoded, claim = pcall(cjson.decode, raw_claim)
        if not decoded or type(claim) ~= "table" or claim.worker_id ~= ARGV[1] then
            return {0, ""}
        end
        local raw_entry = redis.call("GET", KEYS[2])
        local entry_decoded, entry = pcall(cjson.decode, raw_entry)
        if not entry_decoded or type(entry) ~= "table" or entry.status ~= "queued" then
            return {0, ""}
        end
        redis.call("SET", KEYS[2], ARGV[2])
        return {1, ARGV[2]}
    """

    _RECOVER_EXPIRED_CLAIMS_SCRIPT = b"""
        local now = redis.call("TIME")
        local deadline = tonumber(now[1]) * 1000000 + tonumber(now[2])
        local entry_ids = redis.call("ZRANGEBYSCORE", KEYS[1], "-inf", deadline, "LIMIT", 0, ARGV[2])
        local recovered = 0
        local discarded = 0
        for index = 1, #entry_ids do
            local entry_id = entry_ids[index]
            local claim_key = KEYS[2] .. entry_id
            local raw_claim = redis.call("GET", claim_key)
            local decoded, claim = pcall(cjson.decode, raw_claim)
            local lease_deadline = decoded and type(claim) == "table" and tonumber(claim.lease_deadline)
            if not lease_deadline or lease_deadline <= deadline then
                local raw_entry = redis.call("GET", KEYS[4] .. entry_id)
                local entry_decoded, entry = pcall(cjson.decode, raw_entry)
                if entry_decoded and type(entry) == "table" and (entry.status == "queued" or entry.status == "running") then
                    entry.status = "queued"
                    entry.dispatched_at = cjson.null
                    entry.finished_at = cjson.null
                    entry.result = cjson.null
                    entry.error = cjson.null
                    redis.call("SET", KEYS[4] .. entry_id, cjson.encode(entry))
                    if ARGV[1] == "1" then
                        redis.call("LPUSH", KEYS[3], entry_id)
                    else
                        redis.call("RPUSH", KEYS[3], entry_id)
                    end
                    recovered = recovered + 1
                else
                    discarded = discarded + 1
                end
                redis.call("DEL", claim_key)
            else
                redis.call("ZADD", KEYS[1], lease_deadline, entry_id)
            end
            if not lease_deadline or lease_deadline <= deadline then
                redis.call("ZREM", KEYS[1], entry_id)
            end
        end
        return {recovered, discarded}
    """

    _SETTLE_CLAIM_SCRIPT = b"""
        local raw_claim = redis.call("GET", KEYS[1])
        if not raw_claim then
            return 0
        end
        local decoded, claim = pcall(cjson.decode, raw_claim)
        if not decoded or type(claim) ~= "table" or claim.worker_id ~= ARGV[1] then
            return 0
        end
        local raw_entry = redis.call("GET", KEYS[3])
        local entry_decoded, stored_entry = pcall(cjson.decode, raw_entry)
        if not entry_decoded or type(stored_entry) ~= "table" or stored_entry.status ~= "running" then
            return 0
        end
        redis.call("SET", KEYS[3], ARGV[3])
        redis.call("ZREM", KEYS[2], ARGV[2])
        return redis.call("DEL", KEYS[1])
    """

    @dataclass(frozen=True, slots=True)
    class _AsyncScripts:
        """Registered Lua scripts owned by one asynchronous Redis client."""

        claim_entry: object
        acknowledge_claim: object
        renew_claim: object
        mark_claim_running: object
        recover_expired_claims: object
        settle_claim: object

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
        recovery_batch_size = 100

        def __init__(self, redis_spec, options: dict | None = None, **kwargs):
            self._redis = None if isinstance(redis_spec, str) else redis_spec
            self._redis_spec = redis_spec
            self._async_redis_by_loop = {}
            self._async_scripts_by_loop = {}
            options = {} if options is None else options
            options |= kwargs
            if isinstance(redis_spec, str):
                try:
                    connection_kwargs = redis.connection.parse_url(redis_spec)
                except (AttributeError, ValueError) as exc:
                    raise InvalidQueueBackendError(
                        f"Redis URL is invalid: {exc}"
                    ) from exc
            else:
                assert self._redis is not None
                connection_kwargs = self._redis.connection_pool.connection_kwargs
            try:
                self._encoding = codecs.lookup(options.get("encoding", "utf-8")).name
            except (LookupError, TypeError) as exc:
                raise InvalidQueueBackendError("Queue encoding is invalid") from exc
            if (
                connection_kwargs.get("decode_responses", False)
                and self._encoding != "utf-8"
            ):
                raise InvalidQueueBackendError(
                    "A Redis client with decode_responses cannot use a non-UTF-8 "
                    "queue encoding"
                )
            self._queue_name = options.get("queue_name", random_queue_name())
            self._entry_pending_name = f"{self._queue_name}:entries:pending"
            self._entry_claim_prefix = f"{self._queue_name}:entries:claims:"
            self._entry_claim_deadlines_name = (
                f"{self._queue_name}:entries:claim-leases"
            )
            self._connection_encoding = connection_kwargs.get("encoding", "utf-8")
            self._stack = bool(options.pop("stack", False))
            self._maxsize = options.get("maxsize", 0)
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
            self._async_scripts_by_loop[loop] = _AsyncScripts(
                claim_entry=self._register_script(client, _CLAIM_ENTRY_SCRIPT),
                acknowledge_claim=self._register_script(
                    client, _ACKNOWLEDGE_CLAIM_SCRIPT
                ),
                renew_claim=self._register_script(client, _RENEW_CLAIM_SCRIPT),
                mark_claim_running=self._register_script(
                    client, _MARK_CLAIM_RUNNING_SCRIPT
                ),
                recover_expired_claims=self._register_script(
                    client, _RECOVER_EXPIRED_CLAIMS_SCRIPT
                ),
                settle_claim=self._register_script(client, _SETTLE_CLAIM_SCRIPT),
            )
            return client

        def _register_script(self, client, script):
            registered_script = client.register_script(script)
            # Script digests are Redis protocol tokens, not queue values. Keep
            # them as ASCII bytes so non-UTF-8 Redis client encodings cannot
            # corrupt EVALSHA's digest argument.
            registered_script.sha = _encode(registered_script.sha, "ascii")
            return registered_script

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
                self._entry_pending_name, _encode(str(entry.id), "ascii")
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
            return await self.aget_entry(uuid.UUID(_decode(raw_entry_id, "ascii")))

        @property
        def supports_claim_leases(self) -> bool:
            return True

        async def aclaim_entry(
            self,
            worker_id: uuid.UUID,
            lease_seconds: float | None = None,
        ) -> QueueEntry:
            if lease_seconds is None:
                lease_seconds = self.default_claim_lease_seconds
            validate_budget(lease_seconds)
            self._async_redis()
            claim_entry = self._async_scripts_by_loop[
                asyncio.get_running_loop()
            ].claim_entry
            outcome, raw_entry_id = await claim_entry(
                keys=(
                    self._entry_pending_name,
                    _encode(self._entry_claim_prefix, self._connection_encoding),
                    self._entry_claim_deadlines_name,
                ),
                args=(
                    _encode(str(worker_id), "ascii"),
                    _encode(
                        str(round(lease_seconds * MICROSECONDS_PER_SECOND)), "ascii"
                    ),
                    b"1" if self._stack else b"0",
                ),
            )
            outcome = _decode(outcome, "ascii")
            if outcome == "empty":
                raise QueueEmptyException
            if outcome not in {"claimed", "conflict"}:
                raise QueueValueError(f"Unknown Redis claim outcome: {outcome!r}")
            entry_id = uuid.UUID(_decode(raw_entry_id, "ascii"))
            if outcome == "conflict":
                raise QueueClaimConflictError(entry_id)
            try:
                return await self.aget_entry(entry_id)
            except QueueEmptyException as exc:
                raise QueueEntryMissingError(entry_id) from exc

        async def aacknowledge_claim(
            self, entry_id: uuid.UUID, worker_id: uuid.UUID
        ) -> bool:
            self._async_redis()
            acknowledge_claim = self._async_scripts_by_loop[
                asyncio.get_running_loop()
            ].acknowledge_claim
            return bool(
                await acknowledge_claim(
                    keys=(
                        self._entry_claim_key(entry_id),
                        self._entry_claim_deadlines_name,
                    ),
                    args=(
                        _encode(str(worker_id), "ascii"),
                        _encode(str(entry_id), "ascii"),
                    ),
                )
            )

        async def arenew_claim(
            self, entry_id: uuid.UUID, worker_id: uuid.UUID, lease_seconds: float
        ) -> bool:
            validate_budget(lease_seconds)
            self._async_redis()
            renew_claim = self._async_scripts_by_loop[
                asyncio.get_running_loop()
            ].renew_claim
            return bool(
                await renew_claim(
                    keys=(
                        self._entry_claim_key(entry_id),
                        self._entry_claim_deadlines_name,
                    ),
                    args=(
                        _encode(str(worker_id), "ascii"),
                        _encode(
                            str(round(lease_seconds * MICROSECONDS_PER_SECOND)), "ascii"
                        ),
                        _encode(str(entry_id), "ascii"),
                    ),
                )
            )

        async def arecover_expired_claims(self) -> int:
            batch_size = self.recovery_batch_size
            if type(batch_size) is not int or batch_size <= 0:
                raise ValueError("Recovery batch size must be a positive integer")
            self._async_redis()
            recover_expired_claims = self._async_scripts_by_loop[
                asyncio.get_running_loop()
            ].recover_expired_claims
            recovered, discarded = await recover_expired_claims(
                keys=(
                    self._entry_claim_deadlines_name,
                    _encode(self._entry_claim_prefix, self._connection_encoding),
                    self._entry_pending_name,
                    _encode(f"{self._queue_name}:entries:", self._connection_encoding),
                ),
                args=(
                    b"1" if self._stack else b"0",
                    _encode(str(batch_size), "ascii"),
                ),
            )
            if discarded:
                logger.error(
                    "Discarded %s unrecoverable expired queue claim%s",
                    discarded,
                    "s" if discarded != 1 else "",
                )
            return int(recovered)

        async def amark_claim_running(
            self, entry_id: uuid.UUID, worker_id: uuid.UUID
        ) -> QueueEntry | None:
            queued_entry = await self.aget_entry(entry_id)
            running_entry = replace(
                queued_entry,
                status=QueueEntryStatus.RUNNING,
                dispatched_at=await self.clock.anow(),
            )
            self._async_redis()
            mark_claim_running = self._async_scripts_by_loop[
                asyncio.get_running_loop()
            ].mark_claim_running
            marked, _ = await mark_claim_running(
                keys=(self._entry_claim_key(entry_id), self._entry_key(entry_id)),
                args=(
                    _encode(str(worker_id), "ascii"),
                    _encode(json.dumps(running_entry.to_dict()), "ascii"),
                ),
            )
            if not marked:
                return None
            return running_entry

        async def asettle_claim(self, worker_id: uuid.UUID, entry: QueueEntry) -> bool:
            if entry.status not in {
                QueueEntryStatus.SUCCEEDED,
                QueueEntryStatus.FAILED,
                QueueEntryStatus.CANCELLED,
                QueueEntryStatus.TIMEOUT,
            }:
                raise ValueError("A claim can only be settled with a terminal entry")
            self._async_redis()
            settle_claim = self._async_scripts_by_loop[
                asyncio.get_running_loop()
            ].settle_claim
            return bool(
                await settle_claim(
                    keys=(
                        self._entry_claim_key(entry.id),
                        self._entry_claim_deadlines_name,
                        self._entry_key(entry.id),
                    ),
                    args=(
                        _encode(str(worker_id), "ascii"),
                        _encode(str(entry.id), "ascii"),
                        _encode(json.dumps(entry.to_dict()), "ascii"),
                    ),
                )
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

        def _entry_claim_key(self, entry_id: uuid.UUID) -> bytes:
            return _encode(
                self._entry_claim_prefix, self._connection_encoding
            ) + _encode(str(entry_id), "ascii")

        async def _astore_entry(self, entry: QueueEntry) -> None:
            await self._async_redis().set(
                self._entry_key(entry.id), _encode(json.dumps(entry.to_dict()), "ascii")
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
            self._async_scripts_by_loop.pop(loop, None)
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
