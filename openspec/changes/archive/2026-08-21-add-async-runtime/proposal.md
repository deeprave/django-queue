## Why

`queue_observer`'s receiver currently runs on a dedicated thread owned by the
specific `AsyncQueue` *instance* it was registered against
(`_QueueObservers`, `django_queue/observers.py`). Django's queue registry
(`QueueRegistry`, `django_queue/__init__.py`) extends
`BaseConnectionHandler`, which caches queue instances in thread-local storage
by default; only `MemoryEventQueue` opts into `connection_scope = "process"`.
Every `AsyncQueue` backend, Redis included, defaults to `connection_scope =
"thread"`, so two different threads that each first touch the same queue
alias get two independent queue instances — and therefore two independent
receiver threads, each holding its own live Redis pub/sub connection, for
what an application thinks of as one observed queue. This already violates
the `completion-notifications` spec's existing promise that registering an
observer "SHALL automatically start one idempotent local observer runtime."

`EventQueue`'s sibling capability solved exactly this class of problem with
`EventRuntime` (`django_queue/event_runtime.py`): one process-wide background
thread and asyncio loop, started deliberately from `DjangoQueueConfig.ready()`
and Django's `request_started` signal, with per-alias idempotent worker
registration so no matter which thread or how many times startup runs, only
one worker per alias exists. `AsyncQueue` lifecycle observation needs the
same treatment, and once registration happens through a shared, always-running
runtime, an ergonomic decorator form of `queue_observer` — mirroring
`queue_listener`'s existing decorator (`django_queue/listeners.py`) — becomes
possible without repeating the import-time I/O hazard that kept `queue_observer`
a plain function until now. Rather than stand up a second, structurally
near-identical runtime for this, `EventRuntime` itself is renamed to
`QueueRuntime` and extended to host both configured event workers and
observer receivers on its one existing thread and loop — the observer
receiver is thin enough (a pubsub listen loop, no claim/settle/retry) that a
second idle thread in the common case of an application using only one queue
type is waste this change avoids rather than accepts.

## What Changes

- Rename `EventRuntime`/`event_runtime` to `QueueRuntime`/`queue_runtime`
  (`django_queue/event_runtime.py` → `django_queue/queue_runtime.py`) — not
  a breaking change, since neither name is part of this package's public
  `__all__` surface — and extend it to schedule a second per-alias task
  kind — an observer receiver — alongside its existing event-worker tasks,
  on the same shared thread and loop.
- Replace `_QueueObservers`'s per-queue-instance dedicated receiver thread
  with a per-alias task scheduled onto `QueueRuntime`'s shared loop, guarded
  idempotently per alias (mirroring the existing `_start_worker`'s `if alias
  in self._workers: return`), so multiple threads touching the same alias no
  longer produce multiple receivers or Redis connections.
- Preserve each configured queue's own handler/worker strategy: the runtime
  hosts many independent per-alias tasks on one shared loop — it does not
  merge queues into a single shared worker or a single receiver; every alias
  still gets its own task.
- Add decorator-style registration, `@queue_observer("name")`, alongside the
  existing plain-function call `queue_observer("name", callback)`. Modeled on
  `queue_listener`'s decorator shape: a pure in-memory registration recorded
  at decoration/import time (no I/O, safe to run during Python import).
  Actual activation — the backend snapshot fetch and runtime registration
  that `queue_observer` performs today — is deferred until `QueueRuntime`
  starts and walks configured aliases, not triggered by decoration itself.
- Resolve how a decorator-registered observer can still be unsubscribed:
  `queue_listener`'s decorator has no such mechanism today, but
  `queue_observer`'s existing `QueueSubscription.unsubscribe()` contract must
  keep working for both calling conventions.
- Start the runtime's background thread exactly once, from
  `DjangoQueueConfig.ready()`, whenever `QUEUES` is non-empty — not behind
  the `request_started` signal (which was dropped entirely). Per-alias task
  scheduling stays conditional (an `EventQueue` alias always gets a worker;
  an `AsyncQueue` alias only gets a receiver once it has an observer), but
  the thread's existence is gated only on whether `QUEUES` is configured at
  all, via one call path rather than several conditional ones.
- Builds on the separate `fix-redis-observer` change's `aobserve()` (async
  `redis.asyncio` pubsub on `QueueProviderRedis`, already landed): this
  runtime schedules `aobserve()` directly as a task on its shared loop
  instead of bridging through `async_to_sync` on a dedicated thread.
- Add test coverage confirming an `async def` callback registered via the
  new decorator calling convention is dispatched correctly through the
  dispatcher's `inspect.iscoroutinefunction` branch
  (`django_queue/observers.py`). Coverage for the existing direct-call form
  landed separately in `fix-redis-observer`.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `completion-notifications`: registration gains a decorator calling
  convention (`@queue_observer`) alongside the existing function call, and
  the "one idempotent local observer runtime" guarantee becomes genuinely
  alias-scoped and process-wide rather than per-queue-instance, so it holds
  under multi-threaded WSGI deployments.

## Impact

- `django_queue/event_runtime.py` → `django_queue/queue_runtime.py`:
  `EventRuntime`/`event_runtime` renamed to `QueueRuntime`/`queue_runtime`
  and extended with a second per-alias task kind (observer receivers)
  alongside the existing event-worker tasks. Also gains `start_one(alias,
  queue)` (starts one already-resolved alias without re-resolving others —
  required by `create_connection`'s fallback to avoid recursion) and
  `stop_one(alias, timeout=5.0)` (cancels and awaits one alias's task,
  leaving the rest of the runtime untouched — a real capability, not
  test-only, though it was surfaced by a test-cleanliness need).
- `django_queue/observers.py`: `_QueueObservers` (receiver thread replaced by
  a runtime-hosted task), `queue_observer` (adds decorator support).
- `django_queue/apps.py`: `ready()` calls `queue_runtime.start_thread()` and
  `queue_runtime.start(registry)` directly when `QUEUES` is non-empty; the
  `request_started` signal connection is removed.
- `django_queue/__init__.py`: `QueueRegistry.create_connection` calls
  `queue_runtime.start_one(alias, queue)` for a newly-built queue, scheduling
  that alias's task on the thread `ready()` already started.
- `django_queue/listeners.py`: reference only, unchanged — its decorator
  shape is the pattern being mirrored.
- `tests/test_event_runtime.py`: updated for the rename (`EventRuntime` →
  `QueueRuntime`, `event_runtime` → `queue_runtime`); likely renamed to
  `tests/test_queue_runtime.py`.
- Depends on `openspec/changes/fix-redis-observer/` (already landed) for
  `aobserve()` on `QueueProviderRedis`.
- Tests exercising `_QueueObservers`'s receiver-thread lifecycle and
  `queue_observer` registration will need updating to the runtime-task model.
- Internal rename only: `EventRuntime`/`event_runtime` are not part of this
  package's public `__all__` surface, so this is not a breaking change for
  consumers of `django-queues`.
