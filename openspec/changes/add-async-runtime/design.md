## Context

See [proposal.md](proposal.md) for motivation. Relevant current-state facts
this design builds on:

- `_QueueObservers` (`django_queue/observers.py`) is owned by a queue
  *instance* today: `_observers_for(queue)` reads/creates
  `queue._lifecycle_observers`, an attribute on that specific object. It owns
  both the `receiver` thread (the subject of the separate, narrower
  `fix-redis-observer` change) and a `dispatcher` thread (its own private
  loop, a `queue.Queue(maxsize=128)` hand-off, registrations list, and
  sequence counter).
- `QueueRegistry` (`django_queue/__init__.py`) extends Django's
  `BaseConnectionHandler`, caching queue instances in thread-local storage
  (`asgiref.local.Local`) unless a backend opts into `connection_scope =
  "process"`. Only `MemoryEventQueue` does. Every `AsyncQueue` backend
  defaults to `"thread"`, so distinct threads that each first touch the same
  alias get distinct queue instances.
- `EventRuntime` (`django_queue/event_runtime.py`) already solves the
  equivalent problem for `EventQueue`: one process-wide thread, one
  `run_forever` loop, started from `DjangoQueueConfig.ready()` and Django's
  `request_started` signal (`django_queue/apps.py`), with per-alias
  idempotent task registration (`_start_worker`'s `if alias in self._workers:
  return`) so no matter which thread triggers startup, one worker per alias
  survives.
- `queue_listener` (`django_queue/listeners.py`) is a decorator factory that
  appends to a module-level `_listeners` dict — pure in-memory, no I/O, safe
  at import time. `queue_observer` today does real work per call
  (`configured_queue.list()` plus starting the receiver/dispatcher threads on
  first registration), which is why it was never decorator-shaped.
- `fix-redis-observer` (separate, narrower, assumed to land first) adds
  `QueueProviderRedis.aobserve()` — an async method using `redis.asyncio`'s
  pubsub. This change consumes `aobserve()` as a coroutine to schedule on the
  shared loop; it does not re-implement it.

## Goals / Non-Goals

**Goals:**
- One process-wide thread and one asyncio loop hosting one receiver task per
  configured `AsyncQueue` alias that has observers — mirroring `EventRuntime`
  exactly in shape (start/stop lifecycle, per-alias idempotent registration,
  task-done handling).
- Make `_QueueObservers` alias-scoped (one instance per alias, process-wide)
  instead of queue-instance-scoped, so registrations from any thread for the
  same alias are served by the same registration state and the same
  receiver — fixing the root cause, not just relocating the receiver thread.
- Preserve each configured `AsyncQueue`'s independent handler/worker
  strategy: the runtime hosts many independent per-alias receiver tasks on
  one shared loop. It does not merge queues into a single worker, matching
  how `EventRuntime` keeps one task per `EventQueue` alias today.
- Add `@queue_observer(queue_name, entry_id=None)` as a decorator-factory
  calling convention, coexisting with the existing
  `queue_observer(queue_name, callback, entry_id=None)` direct call.
  Decoration itself performs no I/O and starts no thread; activation is
  deferred to runtime start.
- Preserve `QueueSubscription.unsubscribe()` for both calling conventions,
  including unsubscribing a decorator-registered observer before its
  registration has been activated.
- Add test coverage confirming an `async def` callback registered via the
  decorator calling convention is dispatched correctly through the
  dispatcher's `inspect.iscoroutinefunction` branch
  (`django_queue/observers.py`). Direct-call coverage landed separately in
  `fix-redis-observer`.

**Non-Goals:**
- Collapsing the dispatcher (its dedicated thread, private loop, and
  `queue.Queue` hand-off) onto the shared runtime loop. It keeps its current
  internals unchanged; it becomes alias-scoped "for free" as a direct
  consequence of `_QueueObservers` itself becoming alias-scoped, with no
  changes to `publish()`'s thread-safety contract (still callable from
  arbitrary threads — required for the memory backend, which has no receiver
  at all and calls `publish()` directly from whatever thread records a state
  transition). A follow-up change can revisit full unification with
  `EventRuntime`'s inline-dispatch shape later if wanted.
- Adding reconnect/retry for a receiver whose Redis connection drops.
  `fix-redis-observer`'s design explicitly left this out ("no
  reconnect/retry today and this change does not add one"). This change only
  relocates the receiver's threading/ownership model; it does not add
  resilience behavior beyond what exists today. A dropped connection still
  ends that alias's receiver task, which is logged; a later registration for
  that alias starts a fresh one, same as today.
- Unifying `EventRuntime` and the new runtime under a shared base class. See
  Decisions below.
- Changing `QueueRegistry`'s `connection_scope` defaults. The runtime's own
  per-alias idempotent guard makes this unnecessary — the same way
  `EventRuntime` already tolerates Redis-backed `EventQueue` defaulting to
  `connection_scope = "thread"` without needing that setting changed.

## Decisions

### One new `AsyncQueueRuntime`, not a shared base with `EventRuntime`

A new module, `django_queue/async_queue_runtime.py`, defines
`AsyncQueueRuntime` (singleton `async_queue_runtime`), structurally mirroring
`EventRuntime`: `_lock`, `_loop`, `_thread`, `_ready`, a per-alias task dict,
`start(queues)`, `_start()`, `_run_loop()`, `_start_worker`-equivalent,
task-done handling, `shutdown()`.

**Alternative considered — extract a shared base class** (e.g.
`_SingleLoopRuntime` with an abstract "run one alias" hook) **and rejected**:
the two runtimes' per-alias units of work differ enough — `EventRuntime`
wraps `queue.create_worker(alias).run()` with retry/backoff for a worker that
claims, dispatches to listeners, and settles events; the observer receiver
wraps `aobserve()`, a pubsub listen loop with no claim/settle concept and
(per Non-Goals) no retry — that a shared base would need either an abstract
hook that's basically just "the different thing each already does" for
minimal real reuse, or force artificial alignment between two subsystems
that otherwise evolve independently. Duplicating the ~40 lines of
thread/loop bootstrap is cheaper than that coupling.

`apps.py` starts it the same way as `event_runtime`:
```
async_queue_runtime.start(initialise_queues())
```
alongside the existing `event_runtime.start(initialise_queues())`, from both
`DjangoQueueConfig.ready()` and the `request_started` receiver.

### `_QueueObservers` moves from instance-scoped to alias-scoped

`_observers_for` changes from keying off a queue instance's own attribute to
a module-level, alias-keyed store in `observers.py` — mirroring
`listeners.py`'s `_listeners` dict:
```
_observers_by_alias: dict[str, _QueueObservers] = {}
```
guarded by a lock, created once per alias regardless of which thread or how
many queue instances exist for that alias. `queue_observer`'s registration
path (and the runtime's receiver-task scheduling) both resolve through this
alias key rather than through `queue._lifecycle_observers`. This is the
change that actually fixes the bug: without it, relocating only the receiver
thread would leave registration/dispatch state split across per-thread queue
instances, and a receiver on the runtime's loop would have no single,
correct place to call `publish()` into.

### Receiver becomes a per-alias task on the shared loop, not a thread

`AsyncQueueRuntime.start(queues)` iterates configured aliases; for each
`AsyncQueue` alias with at least one registration (direct or decorator,
pending or active), it schedules a receiver task via
`loop.call_soon_threadsafe`, guarded idempotently per alias exactly like
`EventRuntime._start_worker`'s `if alias in self._workers: return`. The task
body is `await self._provider.aobserve(on_snapshot)` (from
`fix-redis-observer`) directly — no `asyncio.run()`, no `async_to_sync()`;
the coroutine already runs on a live loop. Task failure is handled via
`add_done_callback`, mirroring `EventRuntime._worker_done`: pop from the
per-alias task dict, log any exception via `task.result()`. This replaces
`_QueueObservers._run_receiver`'s dedicated `threading.Thread` entirely for
Redis-backed queues. Memory-backed queues are unaffected — they have no
`_observer_receiver` (returns `None`) and never had a receiver thread to
begin with.

### Decorator and direct call coexist via an optional second positional argument

```
def queue_observer(queue_name, callback=None, *, entry_id=None):
    if callback is not None:
        return _register_now(queue_name, callback, entry_id)   # today's behavior, unchanged
    def decorator(fn):
        return _register_deferred(queue_name, fn, entry_id)
    return decorator
```
`_register_now` is today's `queue_observer` body, returning a
`QueueSubscription`. `_register_deferred` performs no I/O: it appends to a
module-level, alias-keyed pending-registration store (same shape as
`listeners._listeners`) and returns `fn` unchanged, per the spec's decorator
scenario.

### Decorator-registered observers get an immediately-usable unsubscribe handle

To satisfy "unsubscribe before or after activation" from the spec, decoration
attaches a small handle object immediately —
`fn._queue_observer_subscription` — not a real `QueueSubscription` yet (none
exists before activation), but an object with the same `.unsubscribe()`
surface. Calling it before activation marks the pending registration
cancelled, so the runtime skips it when it later walks that alias's pending
entries. Calling it after activation delegates to the real
`QueueSubscription` the runtime created. Activation (runtime start, per
alias) replays exactly what `_register_now` already does today —
`configured_queue.list()`, `_QueueObservers.register`/`activate` — for each
non-cancelled pending entry, and binds the resulting real subscription into
the handle.

## Risks / Trade-offs

- [Risk] Making `_QueueObservers` alias-scoped is a real structural change,
  not a pure relocation — tests that reach into `queue._lifecycle_observers`
  directly will need updating to the new alias-keyed lookup. →
  Covered in tasks.md as verification work; same class of update
  `fix-redis-observer` already anticipated for receiver-shape tests.
- [Risk] A receiver task failing (e.g. Redis connection drop) still stops
  observation for that alias with no automatic retry, same as today's
  accepted behavior. → Out of scope per Non-Goals; unchanged from current
  behavior, not a regression.
- [Risk] Two runtimes (`event_runtime`, `async_queue_runtime`) now start from
  the same `ready()`/`request_started` hooks with near-identical bootstrap
  code. → Accepted duplication per the "no shared base" decision above; both
  are small, independently testable, and unlikely to need to change in
  lockstep.

## Migration Plan

No data migration. Depends on `fix-redis-observer` landing first for
`aobserve()`. Existing tests exercising `_QueueObservers`'s receiver-thread
lifecycle, and any test relying on `queue._lifecycle_observers` as a
per-instance attribute, need updating to the alias-scoped model — expected
and tracked in tasks.md, not a design risk. Rollout is a normal code change
behind the test suite; rollback is a plain revert. No feature flag — the
public `queue_observer` API gains a calling convention but loses none, and
existing direct-call registrations keep working unchanged.

## Open Questions

- Whether `EventRuntime` and `AsyncQueueRuntime` should later be unified
  under a shared base once both have been in production for a while and any
  real (not speculative) duplication pain shows up. Deferred; does not
  affect this change's specs, approach, or tasks.
