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

    def _encode(item: str, encoding: str) -> bytes:
        try:
            return item.encode(encoding)
        except UnicodeEncodeError as e:
            raise QueueEncodingException from e

    def _decode(item: bytes, encoding: str) -> str:
        try:
            return item.decode(encoding)
        except UnicodeDecodeError as e:
            raise QueueEncodingException from e

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

        def add(self, *items: str):
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

        def get(self) -> str:
            if self.size() == 0:
                raise QueueEmptyException
            return _decode(self.pop(self._queue_name), self._encoding)

        def poll(self) -> str:
            return _decode(self.bpop([self._queue_name], 0)[1], self._encoding)

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

        def enqueue(self, payload) -> uuid.UUID:
            validate_json_value(payload)
            entry = QueueEntry.create(
                queue=self._queue_name, payload=payload, queued_at=self._clock.now()
            )
            self._store_entry(entry)
            self.push(self._entry_pending_name, _encode(str(entry.id), self._encoding))
            return entry.id

        def get_entry(self, entry_id: uuid.UUID) -> QueueEntry:
            raw_entry = self._redis.get(self._entry_key(entry_id))
            if raw_entry is None:
                raise QueueEmptyException
            return QueueEntry.from_dict(json.loads(raw_entry))

        def dequeue_entry(self) -> QueueEntry:
            raw_entry_id = self.pop(self._entry_pending_name)
            if raw_entry_id is None:
                raise QueueEmptyException
            return self.get_entry(uuid.UUID(_decode(raw_entry_id, self._encoding)))

        def mark_running(self, entry_id: uuid.UUID) -> QueueEntry:
            return self._replace_entry(
                entry_id,
                status=QueueEntryStatus.RUNNING,
                dispatched_at=self._clock.now(),
            )

        def mark_succeeded(self, entry_id: uuid.UUID, result) -> QueueEntry:
            validate_json_value(result)
            return self._replace_entry(
                entry_id,
                status=QueueEntryStatus.SUCCEEDED,
                result=result,
                error=None,
                finished_at=self._clock.now(),
            )

        def mark_failed(self, entry_id: uuid.UUID, error: Exception) -> QueueEntry:
            return self._replace_entry(
                entry_id,
                status=QueueEntryStatus.FAILED,
                error={"type": type(error).__name__, "message": str(error)},
                finished_at=self._clock.now(),
            )

        def mark_cancelled(self, entry_id: uuid.UUID) -> QueueEntry:
            return self._replace_entry(
                entry_id,
                status=QueueEntryStatus.CANCELLED,
                finished_at=self._clock.now(),
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
