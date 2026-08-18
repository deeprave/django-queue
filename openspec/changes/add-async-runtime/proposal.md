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
a plain function until now.

## What Changes

- Add `AsyncQueueRuntime`, mirroring `EventRuntime`'s architecture: one
  background thread and one asyncio loop, process-wide singleton, started
  alongside the existing `event_runtime.start(...)` call in
  `DjangoQueueConfig.ready()` and on Django's `request_started` signal.
- Replace `_QueueObservers`'s per-queue-instance dedicated receiver thread
  with a per-alias task scheduled onto `AsyncQueueRuntime`'s shared loop,
  guarded idempotently per alias (mirroring `EventRuntime._start_worker`'s
  `if alias in self._workers: return`), so multiple threads touching the
  same alias no longer produce multiple receivers or Redis connections.
- Preserve each configured `AsyncQueue`'s own handler/worker strategy: the
  runtime hosts many independent per-alias tasks on one shared loop — it does
  not merge queues into a single shared worker, matching how `EventRuntime`
  already keeps one task per configured `EventQueue` alias.
- Add decorator-style registration, `@queue_observer("name")`, alongside the
  existing plain-function call `queue_observer("name", callback)`. Modeled on
  `queue_listener`'s decorator shape: a pure in-memory registration recorded
  at decoration/import time (no I/O, safe to run during Python import).
  Actual activation — the backend snapshot fetch and runtime registration
  that `queue_observer` performs today — is deferred until
  `AsyncQueueRuntime` starts and walks configured aliases, not triggered by
  decoration itself.
- Resolve how a decorator-registered observer can still be unsubscribed:
  `queue_listener`'s decorator has no such mechanism today, but
  `queue_observer`'s existing `QueueSubscription.unsubscribe()` contract must
  keep working for both calling conventions.
- Builds on the separate `fix-redis-observer` change's `aobserve()` (async
  `redis.asyncio` pubsub on `QueueProviderRedis`): once available, this
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

- `django_queue/observers.py`: `_QueueObservers` (receiver thread replaced by
  a runtime-hosted task), `queue_observer` (adds decorator support).
- `django_queue/apps.py`: start `AsyncQueueRuntime` alongside `event_runtime`.
- New module for `AsyncQueueRuntime` (naming and location to be settled in
  design.md, likely alongside `django_queue/event_runtime.py`).
- `django_queue/listeners.py`: reference only, unchanged — its decorator
  shape is the pattern being mirrored.
- Depends on `openspec/changes/fix-redis-observer/` landing first
  (`aobserve()` on `QueueProviderRedis`).
- Tests exercising `_QueueObservers`'s receiver-thread lifecycle and
  `queue_observer` registration will need updating to the runtime-task model.
