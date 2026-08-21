## Why

`RedisAsyncPriorityQueue`/`RedisAsyncPriorityQueueJson` and
`MemoryAsyncPriorityQueue` only route priority ordering through the raw,
untracked value API (`add`/`aadd` → provider `aadd_priority`/`apoll_priority`,
a Redis sorted set or `queue.PriorityQueue`). The entry-tracked API —
`AsyncQueue.aenqueue()`, which persists a `QueueEntry` and is what `find()`,
`get_result()`, worker dispatch, and lifecycle observation all depend on — is
inherited unchanged from `AsyncQueue` and always calls the provider's plain
FIFO `apush()`/`apop()` (a Redis list / `queue.Queue`), which never consults
priority at all. `QueueEntry` itself has no `priority` field to consult.

Concretely, this means a caller cannot get both entry tracking (required for
status lookups, retries, and Django `django.tasks` integration such as
`django-redis-tasks`) and priority-ordered dispatch at the same time, even
when using the priority queue classes. `tests/test_entry_queue.py`'s own
`test_memory_priority_queue_supports_identified_entries` only asserts a single
enqueued entry round-trips through `dequeue()` — it does not, and today
cannot, assert priority ordering, because none exists on that path.

This gap was found and confirmed while building a `django.tasks` backend on
top of `django-queues` in a sibling project (`django-redis-tasks`,
`migrate-to-django-6-tasks`), which needed `Task.priority` (Django's
queue-dispatch priority, not thread/process priority) to affect dispatch order
for entries it could still track and query. That work settled for
`supports_priority = False` and is waiting on this fix.

## What Changes

- Add a `priority: int` field to `QueueEntry` (default `0`, higher value
  dispatches first — matching the existing raw-path convention in
  `QueueProviderRedis.aadd_priority`/`QueueProviderMemory.aadd_priority`,
  where score/negated-priority ordering already means "higher wins").
- Extend `QueueEntry.create()` and `AsyncQueue.aenqueue()` to accept an
  optional `priority` argument, defaulting to `0` so existing FIFO/stack
  callers are unaffected.
- Give `AsyncQueue` a provider-agnostic way to enqueue/dequeue by priority on
  the *tracked* path (storing the full `QueueEntry`, keyed for priority
  retrieval) rather than only the value-only raw path, and have
  `RedisAsyncPriorityQueue`/`RedisAsyncPriorityQueueJson` and
  `MemoryAsyncPriorityQueue` override `aenqueue`/`adequeue` (not just
  `aadd`/`apoll`) to use it.
- Preserve the existing raw `add`/`aadd`/`poll`/`peek` API and its Redis
  sorted-set / `PriorityQueue` storage unchanged for callers that use the
  untracked value-only interface directly.
- Update `tests/test_entry_queue.py`'s priority fixture coverage (and add
  dedicated ordering tests for `MemoryAsyncPriorityQueue` and
  `RedisAsyncPriorityQueueJson`) to actually assert dispatch-order-by-priority
  on the tracked path, not just round-trip survival.

## Capabilities

### New Capabilities

(none — this extends existing capabilities rather than introducing a new one)

### Modified Capabilities

- `queue-entries`: `QueueEntry` gains a `priority` field; enqueue accepts an
  optional priority argument.
- `async-queue-backends`: priority-variant backends dispatch tracked entries
  in priority order via `aenqueue`/`adequeue`, not plain FIFO order.

## Impact

- `django_queue/entries.py` — `QueueEntry` schema, `create()`.
- `django_queue/backends/base.py` — `AsyncQueue.aenqueue()`/`adequeue()`
  default implementation and its priority extension point.
- `django_queue/backends/redis/provider.py`,
  `django_queue/backends/redis/redispqueue.py`,
  `django_queue/backends/redis/redispqueuejson.py` — tracked-entry priority
  dispatch for Redis.
- `django_queue/backends/memory/provider.py`,
  `django_queue/backends/memory/mempqueue.py` — tracked-entry priority
  dispatch for the memory backend.
- `tests/test_entry_queue.py`, `tests/test_mempqueue.py`,
  `tests/test_redispqueue.py`, `tests/test_redispqueuejson.py`.
- Downstream: unblocks `django-redis-tasks`' `RedisBackend.supports_priority`.
