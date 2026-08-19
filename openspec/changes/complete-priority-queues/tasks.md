## 1. `QueueEntry` schema

- [ ] 1.1 In `django_queue/entries.py`, add `priority: int = 0` to
      `QueueEntry` (after `timeout_seconds`, so every positional/keyword
      caller with `timeout_seconds` already named stays valid).
- [ ] 1.2 Add `priority: int = 0` to `QueueEntry.create()`'s parameters and
      pass it through to the constructed entry.
- [ ] 1.3 Confirm `to_dict`/`from_dict` round-trip `priority` with no
      further change (both iterate `fields(self)` generically) — add a
      direct test if the existing round-trip tests don't already cover an
      added field generically.

## 2. Provider: tracked-entry priority storage

- [ ] 2.1 In `django_queue/backends/redis/provider.py`, add
      `self._entry_pending_priority_name = f"{self._queue_name}:entries:pending:priority"`
      alongside `self._entry_pending_name`'s existing assignment.
- [ ] 2.2 Add `async def apush_priority(self, entry_id, priority) -> None`
      using `zadd(self._entry_pending_priority_name, {entry_id_value: priority})`
      (same encode-then-`zadd` shape as `aadd_priority`, but keyed on the new
      name, storing the entry ID, not an encoded payload).
- [ ] 2.3 Add `async def apop_priority(self) -> QueueEntry`: `zrevrange` +
      `zrem` the highest-scored member off `_entry_pending_priority_name`
      (mirroring `aget_priority`'s shape), decode it to a UUID, then
      `afind()` it — matching `apop`'s existing "pop ID, then look up"
      pattern. Raise `QueueEmptyException` when nothing is pending.
- [ ] 2.4 Add `async def adiscard_priority(self, entry_id) -> None`
      (`zrem` on `_entry_pending_priority_name`), mirroring `adiscard`, for
      the `QUEUED`→`FAILED` cleanup path in `_areplace_entry`.
- [ ] 2.5 In `django_queue/backends/memory/provider.py`, add
      `self._pending_priority: queue.PriorityQueue = queue.PriorityQueue(maxsize=maxsize)`
      alongside `self._pending`'s existing assignment.
- [ ] 2.6 Add `async def apush_priority(self, entry_id, priority) -> None`
      putting `(-int(priority), entry_id)` onto `self._pending_priority`
      (same negation as `aadd_priority`), raising `QueueFullException` on
      `queue.Full`.
- [ ] 2.7 Add `async def apop_priority(self) -> QueueEntry`: pop the
      highest-priority `(neg_priority, entry_id)` off `self._pending_priority`
      under `self._lock`, then look up `self._entries[entry_id]` — mirroring
      `apop`'s existing shape and its `QueueEmptyException`/
      `QueueEntryNotFoundError` handling.
- [ ] 2.8 Add `async def adiscard_priority(self, entry_id) -> None` removing
      `entry_id` from `self._pending_priority` under `self._lock` (mirroring
      `_remove_pending`'s approach for `self._pending`, adapted for the
      `PriorityQueue`'s internal `queue.queue` list plus
      `self._pending_priority.mutex`).

## 3. `AsyncQueue` base: overridable push/pop hooks

- [ ] 3.1 In `django_queue/backends/base.py`, add protected hooks to
      `AsyncQueue`:
      `async def _apush_entry(self, entry: QueueEntry) -> None` calling
      `await self._provider.apush(entry.id)`, and
      `async def _apop_entry(self) -> QueueEntry` calling
      `return await self._provider.apop()`.
- [ ] 3.2 Change `aenqueue()` (`base.py:271`) to call
      `await self._apush_entry(entry)` instead of
      `await self._provider.apush(entry.id)` directly.
- [ ] 3.3 Change `adequeue()` (`base.py:311`) to call
      `return await self._apop_entry()` instead of
      `return await self._provider.apop()` directly.
- [ ] 3.4 Change `_areplace_entry`'s `QUEUED`→`FAILED` cleanup
      (`base.py:395-399`) to call a matching
      `async def _adiscard_entry(self, entry_id: UUID) -> None` hook (default
      `await self._provider.adiscard(entry_id)`) instead of calling
      `self._provider.adiscard(entry_id)` directly, so the priority override
      in Task 4/5 can clean up its own pending store.

## 4. Redis priority queue: route through the tracked hooks

- [ ] 4.1 In `django_queue/backends/redis/redispqueue.py`,
      `RedisAsyncPriorityQueue`: override
      `async def _apush_entry(self, entry) -> None` to call
      `await self._provider.apush_priority(entry.id, entry.priority)`.
- [ ] 4.2 Override `async def _apop_entry(self) -> QueueEntry` to call
      `return await self._provider.apop_priority()`.
- [ ] 4.3 Override `async def _adiscard_entry(self, entry_id) -> None` to
      call `await self._provider.adiscard_priority(entry_id)`.
- [ ] 4.4 Confirm `RedisAsyncPriorityQueueJson` needs no changes — it only
      wraps the raw `aadd`/`aget`/`apoll`/`apeek` value API (encode/decode),
      none of which this change touches.

## 5. Memory priority queue: route through the tracked hooks

- [ ] 5.1 In `django_queue/backends/memory/mempqueue.py`,
      `MemoryAsyncPriorityQueue`: override `_apush_entry`, `_apop_entry`,
      and `_adiscard_entry` the same way as Task 4, calling
      `self._provider.apush_priority`/`apop_priority`/`adiscard_priority`.

## 6. Tests

- [ ] 6.1 In `tests/test_entry_queue.py`, replace or extend
      `test_memory_priority_queue_supports_identified_entries` with a real
      ordering assertion: enqueue a low-priority then a high-priority entry
      through `MemoryAsyncPriorityQueue`, dequeue twice, assert the
      high-priority entry comes first.
- [ ] 6.2 Add an equal-priority ordering test (arrival order preserved
      within the same priority) for `MemoryAsyncPriorityQueue`.
- [ ] 6.3 Add the same two ordering tests (higher-first,
      equal-priority-preserves-arrival) for `RedisAsyncPriorityQueueJson` in
      `tests/test_redispqueuejson.py`, against a real Redis instance.
- [ ] 6.4 Add a test that a priority-enqueued entry dequeued via the
      tracked path is a full `QueueEntry` — findable by `afind()`, and able
      to run through the normal lifecycle transitions (`_amark_running` etc.)
      — for both `MemoryAsyncPriorityQueue` and `RedisAsyncPriorityQueueJson`.
- [ ] 6.5 Add a test that enqueueing without a priority on a priority
      backend defaults to `0` and that a zero-priority entry still dispatches
      (doesn't get stuck behind an always-nonzero assumption).
- [ ] 6.6 Add a test that a non-priority backend (`MemoryAsyncQueue`,
      `RedisAsyncQueue`) ignores a non-zero `priority` passed to `enqueue()`
      and dispatches in its existing FIFO order — covers the "Ignore
      priority on a non-priority backend" spec scenario.
- [ ] 6.7 Add a test that the `QUEUED`→`FAILED` pre-dispatch failure path
      (`_areplace_entry`'s `adiscard` call) removes an entry from the
      priority pending store too, for both backends — i.e. a failed entry
      does not remain dequeuable.
- [ ] 6.8 Run the full suite (`pytest`) to confirm no existing test assumed
      `AsyncQueue.aenqueue`/`adequeue` call `self._provider.apush`/`apop`
      directly (e.g. via mocking) rather than through the new hooks.

## 7. Docs

- [ ] 7.1 Check `README.md`/architecture docs for any description of
      priority queues that describes only the raw value API, and update to
      mention that priority queue *entry* dispatch (via `enqueue`/`dequeue`)
      now also honours priority.
- [ ] 7.2 Add a short note to the `priority` field's meaning (higher value
      dispatches first, default `0`) wherever `timeout_seconds` is
      documented alongside other entry fields, so the two aren't confused.
