## Why

The Redis backend uses the synchronous `redis.Redis` client, so every queue
operation a worker performs is blocking I/O reached through `asyncio.to_thread`.
There are seven such hops across the worker, the ASGI activation poll, and
`runqueues`. An asynchronous worker that leaves the event loop for every dequeue,
every lifecycle transition, and every activation check is not asynchronous in any
useful sense: it is a thread-pool dispatcher wearing a coroutine.

The hops are not only wasteful, they are load-bearing in the wrong direction.
`add-timeout-governance` could not ship its heartbeat because of them. Extending
a live budget must extend the backend's lease and verify the calling handler
still owns it, and neither is safe across a thread boundary — `Timeout.reschedule`
mutates a `TimerHandle` that is not thread-safe. That requirement was removed
from `add-timeout-governance` and belongs here, to the change that can implement
it.

## What Changes

- Move the Redis backends from `redis.Redis` to `redis.asyncio.Redis`, awaiting
  every command rather than dispatching it to a thread.
- Make the `BaseQueue` contract asynchronous, so memory and Redis backends
  present one contract and the worker can await a backend directly.
- Add an asynchronous surface named by Django's own convention: `aenqueue`,
  `aget_entry`, `adequeue_entry`, `ahas_pending_entries`, `amark_running`,
  `amark_succeeded`, `amark_failed`, `amark_cancelled`, `amark_timed_out`,
  `aclose`. These are the primary implementations.
- Keep every existing synchronous name working as a thin wrapper over its
  asynchronous counterpart, bridged with `asgiref.sync.async_to_sync`, so a
  synchronous Django view calling `queue.enqueue(...)` is unaffected. Calling a
  synchronous name from inside a running event loop raises, matching how the
  Django ORM refuses the same mistake.
- Remove all seven `asyncio.to_thread` hops from `django_queue.worker`,
  `django_queue.asgi`, and `runqueues`.
- Use `asgiref.sync.async_to_sync` and `sync_to_async` for every synchronous and
  asynchronous crossing in the package. No `asyncio.to_thread`, no hand-rolled
  bridge.
- Add the heartbeat deferred from `add-timeout-governance`, as far as it can
  honestly go here: a call a handler makes to assert progress, which restarts
  its execution budget, raises outside an active dispatch, and refuses to be
  used as a keepalive. It does **not** validate lease ownership, because there
  is no lease yet — `add-redis-claim-ack` introduces the ownership boundary and
  `add-redis-lease-recovery` makes it expire. The ownership check is added to
  the heartbeat by that change, and this change's requirement says so rather
  than implying a guarantee it does not provide. Nothing can reclaim an entry
  today, so a budget-only heartbeat is correct now and incomplete later.
- **BREAKING** for custom backends: a third-party `BaseQueue` subclass must
  implement the asynchronous methods. The synchronous names it implements today
  are no longer the extension point.

## Capabilities

### New Capabilities

- `async-queue-backends`: The asynchronous backend contract — which operations
  are awaitable, the `a`-prefixed naming rule, the synchronous wrappers and when
  they refuse, connection lifecycle and disposal, and the requirement that
  crossings use the framework's bridges rather than raw thread dispatch.

### Modified Capabilities

- `async-queue-workers`: "Keep queue I/O from blocking the event loop" inverts.
  The worker no longer executes synchronous queue operations off the loop; it
  awaits the backend directly, and the backend is what keeps the loop free.
- `queue-entries`: The enqueue and lifecycle operations become awaitable, with
  synchronous wrappers. The entry record itself does not change.
- `timeout-governance`: Add "Extend a budget from a live handler", removed from
  `add-timeout-governance` because it could not be implemented there, scoped to
  budget extension only until a lease exists to check ownership against.
- `asgi-process-worker`: The local activation poll awaits the backend rather
  than dispatching `has_pending_entries` to a thread.
- `queue-worker-cli`: `runqueues` awaits its activation wait for the same
  reason.
- `configured-queue-registry`: Queue disposal becomes asynchronous, since an
  `redis.asyncio` connection pool must be closed from the loop that owns it. The
  `close_queues` signal receiver stays synchronous and bridges.

## Impact

Depends on `add-timeout-governance` archiving first: this change modifies the
`timeout-governance` capability that change introduces, and implements the
requirement deferred out of it.

Affected code: `django_queue/backends/base.py`, both Redis backends, all three
memory backends, `django_queue/worker.py`, `django_queue/asgi.py`,
`django_queue/management/commands/runqueues.py`, `django_queue/__init__.py`.

Dependencies: `redis.asyncio` ships in the already-pinned redis 8.1.0, and
`asgiref` 3.12.1 is already present as a Django dependency. Nothing new is
added.

Public API: every existing synchronous name keeps working, so application code
that enqueues from a synchronous view needs no change. Custom backend
implementations are the breaking surface. The package has no released consumers.
