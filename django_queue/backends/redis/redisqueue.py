try:
    import json
    import uuid
    from dataclasses import replace

    import redis

    from django_queue.backends.base import BaseQueue
    from django_queue.backends.exceptions import (
        QueueEmptyException,
        QueueEncodingException,
        QueueFullException,
    )
    from django_queue.clock import RedisQueueClock
    from django_queue.entries import QueueEntry, QueueEntryStatus, validate_json_value
    from django_queue.signals import send_entry_enqueued

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

    class RedisQueue(BaseQueue):
        def __init__(self, redis_spec, options: dict | None = None, **kwargs):
            self._redis = (
                redis.from_url(redis_spec)
                if isinstance(redis_spec, str)
                else redis_spec
            )
            options = {} if options is None else options
            options |= kwargs
            self._queue_name = options.get("queue_name", random_queue_name())
            self._entry_pending_name = f"{self._queue_name}:entries:pending"
            self._stack = bool(options.pop("stack", False))
            self._maxsize = options.get("maxsize", 0)
            self._encoding = options.get("encoding", "utf-8")
            self._clock = RedisQueueClock(self._redis)
            self.push = self._redis.rpush
            self.pop = self._redis.rpop if self._stack else self._redis.lpop
            self.bpop = self._redis.brpop if self._stack else self._redis.blpop

        @property
        def stack(self):
            return self._stack

        @property
        def capacity(self):
            return self._maxsize

        # The raw-item API is heterogeneous across this family: JSON variants
        # exchange dicts and priority variants (priority, item) tuples. Leaf
        # classes carry the precise annotations.
        def add(self, *items):
            if items:
                current_size = self.size()
                if self._maxsize != 0 and current_size + len(items) > self._maxsize:
                    raise QueueFullException
                self.push(
                    self._queue_name,
                    *(
                        _encode(item, self._encoding)
                        for item in items
                        if item is not None
                    ),
                )

        def get(self):
            # The size check and the pop are not atomic, so a competing consumer
            # can empty the queue in between; treat that as empty rather than
            # letting None reach the decoder.
            if self.size() == 0:
                raise QueueEmptyException
            item = self.pop(self._queue_name)
            if item is None:
                raise QueueEmptyException
            return _decode(item, self._encoding)

        def poll(self):
            # A zero timeout blocks indefinitely, so this should not return
            # empty-handed; guard rather than subscript a possible None.
            item = self.bpop([self._queue_name], 0)
            if not item:
                raise QueueEmptyException
            return _decode(item[1], self._encoding)

        def peek(self):
            if self.size() == 0:
                raise QueueEmptyException
            if self._stack:  # LIFO: Peek last (rightmost) item
                return _decode(
                    self._redis.lrange(self._queue_name, -1, -1)[0], self._encoding
                )
            else:  # FIFO: Peek first (leftmost) item
                return _decode(
                    self._redis.lrange(self._queue_name, 0, 0)[0], self._encoding
                )

        def size(self):
            return self._redis.llen(self._queue_name)

        def clear(self):
            self._redis.delete(self._queue_name)

        def enqueue(
            self, payload, *, timeout_seconds: float | None = None
        ) -> uuid.UUID:
            validate_json_value(payload)
            entry = self.entry_class.create(
                queue=self._queue_name,
                payload=payload,
                queued_at=self.clock.now(),
                timeout_seconds=timeout_seconds,
            )
            self._store_entry(entry)
            self.push(self._entry_pending_name, _encode(str(entry.id), self._encoding))
            send_entry_enqueued(self, entry=entry)
            return entry.id

        def get_entry(self, entry_id: uuid.UUID) -> QueueEntry:
            raw_entry = self._redis.get(self._entry_key(entry_id))
            if raw_entry is None:
                raise QueueEmptyException
            return self.entry_class.from_dict(json.loads(raw_entry))

        def dequeue_entry(self) -> QueueEntry:
            raw_entry_id = self.pop(self._entry_pending_name)
            if raw_entry_id is None:
                raise QueueEmptyException
            return self.get_entry(uuid.UUID(_decode(raw_entry_id, self._encoding)))

        def has_pending_entries(self) -> bool:
            return bool(self._redis.llen(self._entry_pending_name))

        def mark_running(self, entry_id: uuid.UUID) -> QueueEntry:
            return self._replace_entry(
                entry_id,
                status=QueueEntryStatus.RUNNING,
                dispatched_at=self.clock.now(),
            )

        def mark_succeeded(self, entry_id: uuid.UUID, result) -> QueueEntry:
            validate_json_value(result)
            return self._replace_entry(
                entry_id,
                status=QueueEntryStatus.SUCCEEDED,
                result=result,
                error=None,
                finished_at=self.clock.now(),
            )

        def mark_failed(self, entry_id: uuid.UUID, error: Exception) -> QueueEntry:
            return self._replace_entry(
                entry_id,
                status=QueueEntryStatus.FAILED,
                error={"type": type(error).__name__, "message": str(error)},
                finished_at=self.clock.now(),
            )

        def mark_cancelled(self, entry_id: uuid.UUID) -> QueueEntry:
            return self._replace_entry(
                entry_id,
                status=QueueEntryStatus.CANCELLED,
                finished_at=self.clock.now(),
            )

        def mark_timed_out(self, entry_id: uuid.UUID) -> QueueEntry:
            return self._replace_entry(
                entry_id,
                status=QueueEntryStatus.TIMEOUT,
                finished_at=self.clock.now(),
            )

        def _entry_key(self, entry_id: uuid.UUID) -> str:
            return f"{self._queue_name}:entries:{entry_id}"

        def _store_entry(self, entry: QueueEntry) -> None:
            self._redis.set(self._entry_key(entry.id), json.dumps(entry.to_dict()))

        def _replace_entry(
            self, entry_id: uuid.UUID, *, status: QueueEntryStatus, **changes
        ) -> QueueEntry:
            if not isinstance(status, QueueEntryStatus):
                raise TypeError("Queue entry status must be a QueueEntryStatus")
            previous_entry = self.get_entry(entry_id)
            if status not in previous_entry.status.next_state():
                raise ValueError(
                    f"Cannot transition queue entry from {previous_entry.status} to {status}"
                )
            entry = replace(previous_entry, status=status, **changes)
            self._store_entry(entry)
            return entry

    class RedisStack(RedisQueue):
        def __init__(self, redis_spec, options: dict | None = None, **kwargs):
            options = {} if options is None else options
            options |= kwargs
            options.setdefault("stack", True)
            super().__init__(redis_spec, options)

except ImportError:
    pass
