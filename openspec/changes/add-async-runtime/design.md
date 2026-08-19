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
  survives. This change renames and extends it in place — see Decisions.
- `queue_listener` (`django_queue/listeners.py`) is a decorator factory that
  appends to a module-level `_listeners` dict — pure in-memory, no I/O, safe
  at import time. `queue_observer` today does real work per call
  (`configured_queue.list()` plus starting the receiver/dispatcher threads on
  first registration), which is why it was never decorator-shaped.
- `fix-redis-observer` (separate, narrower, already landed) adds
  `QueueProviderRedis.aobserve()` — an async method using `redis.asyncio`'s
  pubsub. This change consumes `aobserve()` as a coroutine to schedule on the
  shared loop; it does not re-implement it.

## Goals / Non-Goals

**Goals:**
- One process-wide thread and one asyncio loop hosting both configured event
  workers and observer receivers: one `_run_worker` task per `EventQueue`
  alias (existing `EventRuntime` behaviour, unchanged), plus one
  `_run_receiver` task per `AsyncQueue` alias that has observers (new).
- Make `_QueueObservers` alias-scoped (one instance per alias, process-wide)
  instead of queue-instance-scoped, so registrations from any thread for the
  same alias are served by the same registration state and the same
  receiver — fixing the root cause, not just relocating the receiver thread.
- Preserve each configured queue's independent handler/worker strategy: the
  runtime hosts many independent per-alias tasks on one shared loop. It does
  not merge queues into a single worker or a single receiver — every alias
  still gets its own task, same as `EventRuntime` does today for event
  queues.
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
  transition).
- Adding reconnect/retry for a receiver whose Redis connection drops.
  `fix-redis-observer`'s design explicitly left this out ("no
  reconnect/retry today and this change does not add one"). This change only
  relocates the receiver's threading/ownership model; it does not add
  resilience behavior beyond what exists today. A dropped connection still
  ends that alias's receiver task, which is logged; a later registration for
  that alias starts a fresh one, same as today. Because event workers and
  observer receivers now share one loop (see Decisions), a receiver task's
  own failure handling — logged and dropped, no retry — must not raise past
  its `add_done_callback`, so it cannot destabilise the loop event workers
  depend on.
- Changing `QueueRegistry`'s `connection_scope` defaults. The runtime's own
  per-alias idempotent guard makes this unnecessary — the same way the event
  side already tolerates Redis-backed `EventQueue` defaulting to
  `connection_scope = "thread"` without needing that setting changed.

## Decisions

### Unify into one `QueueRuntime` (renamed from `EventRuntime`), not a second runtime

`django_queue/event_runtime.py` is renamed to `django_queue/queue_runtime.py`;
`EventRuntime`/`event_runtime` become `QueueRuntime`/`queue_runtime`. Rather
than adding a structurally-mirrored second runtime (`AsyncQueueRuntime`, one
more thread, one more loop, one more singleton), `QueueRuntime` gains a
second per-alias task kind on its existing loop: alongside `_run_worker`
(today's event-queue dispatch, unchanged), a new `_run_receiver` schedules
`aobserve()` for each `AsyncQueue` alias with observers. Both task kinds
share the same `_lock`, `_loop`, `_thread`, `_ready`, and per-alias task dict
— `start(queues)` walks `queues` once and schedules whichever task kind
applies per alias (`_run_worker` for an `EventQueue`, `_run_receiver` for an
`AsyncQueue` with a registration), rather than two separate `start()` calls
against two separate objects.

**Revises an earlier version of this design**, which proposed a second,
structurally-mirrored `AsyncQueueRuntime` and explicitly rejected only a
*shared base class* between two runtime instances. Re-examined: `Goals` here
never asked for two loops, and `AsyncQueueRuntime` as designed was thin
enough (no claim/settle/retry, just a pubsub listen loop) that running it as
a second full thread+loop mostly just idled when either queue type was
absent, and duplicated ~40 lines of thread/loop bootstrap that a single
merged runtime does not need to duplicate at all. Unifying removes that idle
cost and the "two runtimes started from near-identical bootstrap code" risk
entirely, rather than accepting it.

**Trade-off accepted**: event-worker and observer-receiver tasks now share
one cooperative asyncio loop. `asyncio.create_task()` scheduling is already
non-blocking-by-default between tasks — they interleave at `await` points,
so an ordinarily-behaved task never stalls another just by running
alongside it on the same loop; this is not a new isolation model, it is the
model `_run_worker` already relies on today. Two things narrow where the
real risk lives:

- **User observer callbacks — the code most likely to do database work,
  bridge through `async_to_sync`, or touch Django Channels — never run on
  this loop at all.** They run on `_QueueObservers`'s existing dispatcher
  thread (`_run_dispatcher`, `django_queue/observers.py`), which owns its
  own dedicated `threading.Thread` and private event loop, unchanged by
  this design (see Non-Goals). `aobserve()`'s task body only calls
  `on_snapshot` (`_QueueObservers.publish`), which is lock-protected,
  non-blocking, and does no I/O of its own — it enqueues onto a bounded
  `queue.Queue` (`put_nowait`) for the dispatcher thread to actually invoke
  callbacks from. So the shared-loop coupling this decision introduces does
  not extend to arbitrary user code.
- **The residual risk is narrower and specific**: `aobserve()` itself
  (`redis.asyncio`'s `pubsub.listen()`) failing to yield at all during some
  internal operation — a `redis-py` bug, a genuinely stuck synchronous call
  inside its internals, or a blocking call introduced by a third-party
  `ENTRY_CLASS`/backend override. Ordinary asyncio cooperative scheduling
  cannot protect against a task that never awaits; only true isolation
  (a separate OS thread) fully closes this. This is accepted, not engineered
  around: `pubsub.listen()` is a well-behaved async generator with no known
  blocking-without-yielding failure mode in normal operation, and adding a
  watchdog/timeout or a second isolated loop for a currently-theoretical
  failure mode is judged not worth the added complexity today. If production
  experience ever shows a receiver task stalling event-worker dispatch on
  the same loop, that is the trigger to revisit — with a concrete incident
  to design against, not a speculative one.

The previously-considered two-loop isolation would have avoided even this
narrower coupling entirely, but at the cost of a second idle thread in the
common case where an application predominantly uses one queue type — judged
not worth it for this project's scale, given how thin the actual exposure
is once the dispatcher's existing isolation is accounted for.

This isolation story covers the observer-receiver side specifically. A
separate, pre-existing gap on the event-worker side — an *async*
`queue_listener` callback already runs inline on this shared loop, with no
isolation, unlike a sync callback (see the corresponding Risk below) — is
not introduced by this decision, but this decision does mean that gap's
blast radius now also reaches observer delivery, not just other event-queue
workers. See Risks / Trade-offs for why that is documented rather than
fixed as part of this change.

### One entry point starts the thread; task scheduling is separate and stays conditional

**Supersedes an earlier version of this design**, which started the thread
implicitly as a side effect of `start()`/`start_one()` scheduling a task
(itself triggered from two places — the `request_started` signal and
`QueueRegistry.create_connection` — whichever fired first). Revisited after
implementation: that coupling meant the thread's existence was an emergent
property of two separate, conditionally-firing call sites, rather than a
single deliberate decision. A pragmatic simplification, agreed with the
user: if `QUEUES` is configured at all, the process needs the runtime
thread — full stop, independent of *which* aliases end up scheduling tasks
on it. Splitting "does the thread exist" from "which tasks are scheduled on
it" removes that coupling entirely.

`QueueRuntime.start_thread()` is the *sole* method that creates the
background thread and loop (idempotent — a no-op if already started).
Called exactly once, from `DjangoQueueConfig.ready()`:

```python
# apps.py
def ready(self) -> None:
    from django_queue import initialise_queues
    from django_queue.queue_runtime import queue_runtime

    registry = initialise_queues()
    if registry.settings:
        queue_runtime.start_thread()
        queue_runtime.start(registry)
```

`ready()` already called `initialise_queues()` unconditionally, which
force-resolves every configured alias — exactly what's needed to both know
whether `QUEUES` is non-empty and to have every queue object on hand.
`request_started` is no longer connected at all; `ready()` runs once per
process, before any request or out-of-process code path touches a queue, so
there is no longer a "process that never fires `request_started`" gap to
patch — the thread is already up by the time anything could ask for it.

`start()`/`start_one()` keep their existing per-alias classification and
task-scheduling logic (an `EventQueue` alias always gets a worker; an
`AsyncQueue` alias gets a receiver only if it has an observer registration —
see `_classify` below) completely unchanged, but no longer create the
thread themselves — `_start_classified` schedules onto `self._loop` if it
exists and no-ops otherwise, rather than calling a thread-bootstrap method.
`QueueRegistry.create_connection` still calls `queue_runtime.start_one(alias,
queue)` after building a queue (see the `start_one` decision below for why
`start_one`, not `start`), and direct-call `queue_observer()` still calls
`start_one` too — both now purely schedule that alias's task on a thread
that `ready()` already started, rather than being one of two competing
triggers for the thread's existence.

### Test-only, opt-in suppression fixture — not a setting, not an env var

Routing the fallback through `QueueRegistry.create_connection` means *any*
queue built through the registry — including in-memory queues most of this
project's own test suite constructs — now calls `queue_runtime.start(self)`.
`start()` already filters to aliases with an actual worker/observer to run
and no-ops otherwise, so most tests are unaffected in practice; but a fixture
that happens to configure a `HANDLER` or register a `queue_observer` would
now start a real background thread a test never asked for, with no built-in
teardown — six existing test files touch the registry directly
(`test_configured_queues.py`, `test_event_listeners.py`,
`test_entry_queue.py`, `test_event_runtime.py`, `test_redis_entries.py`,
`test_runqueues.py`).

This is test-harness plumbing, not an application concern — no Django
setting and no environment variable, both of which would be reachable (and
meaningful, and needing documentation) from production code. Instead, one
function-scoped, **not autouse** pytest fixture in `tests/conftest.py`,
suppressing the runtime by monkeypatching its module-level singleton's bound
`start` method to a no-op:

```python
@pytest.fixture
def no_runtime_startup(monkeypatch):
    """Suppress queue_runtime auto-start via ready()/QueueRegistry.create_connection."""
    from django_queue.queue_runtime import queue_runtime
    monkeypatch.setattr(queue_runtime, "start_thread", lambda: None)
    monkeypatch.setattr(queue_runtime, "start", lambda queues: None)
    monkeypatch.setattr(queue_runtime, "start_one", lambda alias, queue: None)
```

(`start_thread` and `start_one` are patched alongside `start` because
`DjangoQueueConfig.ready()` calls `start_thread` directly — see the
"One entry point starts the thread" decision — and `create_connection`'s
fallback calls `start_one` specifically — see the `start_one` decision
below — not `start`.)

Unifying the two runtimes collapses what would otherwise have needed to be
two independent suppression fixtures (one per runtime) into one — a test
that wants a real runtime running (e.g. `test_event_runtime.py`'s tests,
which already construct throwaway `EventRuntime()`/`QueueRuntime()`
instances directly rather than touching the singleton) simply doesn't
request this fixture; there is no longer a need to suppress "the other
runtime" independently, because there is only one. `monkeypatch` reverts
automatically per test, so the fixture never leaks state regardless of which
tests request it, and — per explicit decision — it is **not autouse**:
suppression is opt-in per test, not a suite-wide default, so a test that
constructs a registry-backed queue without requesting it still gets real
(filtered, mostly no-op) `.start()` behavior, exercising the actual fallback
path rather than hiding it everywhere by default.

**Confirmed empirically, not just by inspection, that this matters**:
instrumenting a thread-count check across the full suite found real,
previously-unnoticed background-thread leaks in `test_event_listeners.py`
and `test_configured_queues.py` — both resolve an `EventQueue` alias
through the registry (via `queue_listener`'s own eager `queues[alias]`
lookup, or via `initialise_queues()` force-resolving every alias), which
unconditionally starts a worker task, entirely independent of whether
`queue_observer` is involved at all. A leaked daemon thread does not fail a
test by itself, so this class of gap does not show up as a red test — only
as accumulating background threads across a full suite run.

### `start_one`, not `start`, from `create_connection` — and why direct-call `queue_observer` also needs it

Two related refinements surfaced only once the fallback was actually
exercised by tests, not from reasoning about the design alone:

**`create_connection` recurses if it calls `start(self)`.** Confirmed via an
actual `RecursionError` running the test suite: `BaseConnectionHandler.__getitem__`
only caches an alias's queue *after* `create_connection` returns, so `start()`'s
own `queues[alias]` loop (which resolves every configured alias, not just the
one just built) re-enters `create_connection` for that same still-uncached
alias — which calls `start(self)` again, forever. Fixed by adding
`QueueRuntime.start_one(alias, queue)`: starts the task for one
already-resolved alias directly, without walking or re-resolving any other
alias through `queues[alias]`. `start()` and `start_one()` share a
`_classify`/`_start_classified` helper pair rather than duplicating the
type/registration-filtering logic. `create_connection` calls
`queue_runtime.start_one(alias, queue)` with the queue it just built, never
`queue_runtime.start(self)`.

**Direct-call `queue_observer()` also needed to schedule a receiver task, not
only `create_connection`.** Not anticipated in the original design:
`_register_now` (the direct-call registration path) previously only touched
`_observers_by_alias`, never the runtime — so an alias whose *only*
registration arrived via a direct `queue_observer(alias, callback)` call,
made *after* `start`/`start_one` had already looked at (and skipped, for
lack of any registration at the time) that alias, would never get a
receiver task at all. Found via a real test failure
(`test_pruning_publishes_a_terminated_snapshot_to_an_observer`, which
registers directly, after the queue is already resolved), not by
inspection. `_register_now` now calls `queue_runtime.start_one(queue_name,
configured_queue)` after registering, mirroring `create_connection`'s call.

This introduced a second, structurally similar recursion: `_register_now`'s
own `start_one` call re-enters `_classify` → `_activate_pending_for(alias)`
(activation for decorator-pending entries lives in `_classify`, since it
must run synchronously, off the event loop — see the "Receiver becomes a
per-alias task" decision below) → which would try to activate the *same*
still-in-flight pending entry again, calling `_register_now` again, forever.
Fixed by adding an `activating: bool` flag to `_PendingRegistration`, set
*before* calling `_register_now`, so re-entrant activation checks correctly
treat an in-flight activation as already handled rather than still pending.

### `stop_one`: scoped, awaited per-alias teardown — added for tests, but real runtime capability

`QueueRuntime` had no way to stop a single alias's task short of
`shutdown()`, which is far too blunt for anything but full process
teardown: it permanently sets `self._closed = True` on the module-level
singleton (no way to un-close it), stops the shared thread/loop entirely —
cancelling *every* alias's task, not just one — and would silently turn
every subsequent `start()`/`start_one()` call for the rest of the process
into a no-op. Confirmed this concretely: calling `shutdown()` from inside
one test would poison `queue_runtime` for every test running afterward in
the same process, an unacceptable degree of cross-test interference for
what only needed to stop one alias.

`stop_one(alias, timeout=5.0)` cancels and — critically — *awaits* one
alias's task via `asyncio.run_coroutine_threadsafe`, rather than firing
`task.cancel()` and returning immediately. This matters because a
cancelled task's own cleanup (e.g. `aobserve()`'s `finally: await
pubsub.aclose(); await client.aclose()`) is itself async work that runs
*after* cancellation is requested, not synchronously with it — a bare,
unawaited `task.cancel()` would return before that cleanup finishes,
recreating the same race it exists to close (specifically: a caller
proceeding to tear down a shared resource, such as a test's Redis
container, while the cancelled task is still using it). `stop_one` leaves
the shared thread/loop and every other alias untouched — unlike
`shutdown()`, other tests or production code relying on the singleton are
unaffected.

This is not test-only scaffolding bolted on for convenience: "stop
observing/dispatching this one queue without tearing down the whole
runtime" is a real capability an application might reasonably want too
(e.g. dynamically deconfiguring a queue alias at runtime), so it is
designed and documented as permanent `QueueRuntime` API, not a private test
helper.

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

`QueueRuntime.start(queues)` iterates configured aliases; for each
`AsyncQueue` alias with at least one registration (direct or decorator,
pending or active), it schedules a receiver task via
`loop.call_soon_threadsafe`, guarded idempotently per alias exactly like
`_start_worker`'s existing `if alias in self._tasks: return` (generalised
from `self._workers` to a single `self._tasks` dict shared by both task
kinds, keyed by alias — an alias is either an event queue or an async
queue, never both, so one dict is unambiguous). The task body is `await
self._provider.aobserve(on_snapshot)` (from `fix-redis-observer`) directly —
no `asyncio.run()`, no `async_to_sync()`; the coroutine already runs on a
live loop. Task failure is handled via `add_done_callback`, mirroring the
existing `_worker_done`: pop from the per-alias task dict, log any exception
via `task.result()`, and — per the Non-Goals note on shared-loop
stability — never re-raise into the loop. This replaces
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
- [Risk, fixed] `_QueueObservers._run_dispatcher`'s dispatcher thread had no
  shutdown path anywhere in the codebase, and alias-scoping made a
  pre-existing gap materially worse: previously the dispatcher was owned by
  a queue *instance*, so it died with that instance's normal lifecycle in
  most usage patterns; `_observers_by_alias` being a process-global dict
  keyed by alias meant anything that popped and recreated an alias's entry
  (test fixtures doing state-isolation cleanup, in particular) spun up a
  new, permanently-running dispatcher thread with no way to reach and stop
  the old one. Confirmed empirically: a full local test-suite run left
  dozens of live `django-queues-observers-*` threads afterward. → Fixed,
  not deferred: added `_QueueObservers.stop()` (enqueues a sentinel to
  unblock the dispatcher's blocking `events.get()`, then joins the thread —
  mirrors `QueueRuntime.stop_one`'s cancel-and-await shape) and
  `_discard_observers_for(alias)` (the one correct way to discard an
  alias's `_QueueObservers`: pops it from `_observers_by_alias` *and* stops
  its dispatcher, rather than leaving a popped-but-still-running thread
  behind). Every test call site that previously did
  `_observers_by_alias.pop(alias, None)` directly now calls
  `_discard_observers_for(alias)` instead. Verified empirically: the same
  full-suite thread count went from dozens to zero. A running application
  still never needs this (one alias's `_QueueObservers` is meant to live
  for the process lifetime), so `stop()` is unused in production code paths
  today, but it is real, documented, permanent API — not test-only
  scaffolding — consistent with how `QueueRuntime.stop_one` was added
  earlier in this same change for the same class of problem.
- [Risk] Event workers and observer receivers now share one loop — a
  receiver task that fails to yield at all (not ordinary cooperative
  scheduling, which already isolates well-behaved tasks) could stall event
  dispatch, where two separate loops would have isolated them completely. →
  Accepted per the "Unify into one `QueueRuntime`" decision above: user
  observer callbacks (the code most likely to block or do I/O) never run on
  this loop — they run on `_QueueObservers`'s existing, unchanged dispatcher
  thread. The narrower residual risk is `aobserve()`'s own `pubsub.listen()`
  failing to yield, which has no known trigger in normal operation; failure
  handling never re-raises into the shared loop either way. Revisit with a
  concrete incident, not speculatively.
- [Risk] **Pre-existing, not introduced by this change, but extended by it**:
  `EventQueueWorker._invoke` (`django_queue/event_worker.py:172-178`)
  dispatches an *async* `queue_listener` callback directly, inline, awaited
  straight through `_dispatch` on `EventRuntime`'s (now `QueueRuntime`'s)
  one shared loop — unlike a *sync* callback, which already goes through
  `sync_to_async`'s thread-pool executor and is isolated. A slow-running
  async listener (a disguised blocking call, or just legitimately long
  async work) already stalls that shared loop today, delaying every other
  configured `EventQueue` alias's worker on it — independent of this
  change. What this change adds is that the same shared loop, once
  unified, also hosts observer-receiver tasks, so a slow async event
  listener would newly also delay `AsyncQueue` lifecycle-observation
  delivery. → **Documented, not fixed here.** `EventQueueWorker._invoke`
  is a different subsystem than this change's scope (observer
  registration/runtime, not event-listener dispatch), and fixing it
  correctly (isolating async callbacks the way sync ones already are)
  deserves its own scoped change rather than a side-effect of this one.
  Tracked as a follow-up; not a blocker for unifying the runtimes, since
  the coupling this change adds is strictly smaller than the coupling
  that already exists between `EventQueue` aliases sharing one loop today.
- [Risk] Renaming `EventRuntime`/`event_runtime` to `QueueRuntime`/
  `queue_runtime` touches every existing import site
  (`django_queue/apps.py`, `tests/test_event_runtime.py`, any other internal
  reference) — a larger diff than purely additive work. → Accepted: the
  rename is mechanical (tracked in tasks.md), and shipping one correctly-named
  runtime now is preferred over shipping a second, differently-named one and
  renaming later under greater migration pressure.

## Migration Plan

No data migration. Depends on `fix-redis-observer` (already landed) for
`aobserve()`. Existing tests exercising `_QueueObservers`'s receiver-thread
lifecycle, any test relying on `queue._lifecycle_observers` as a
per-instance attribute, and any internal reference to `EventRuntime`/
`event_runtime` by name need updating to the renamed, unified model —
expected and tracked in tasks.md, not a design risk. Rollout is a normal
code change behind the test suite; rollback is a plain revert. No feature
flag — the public `queue_observer` API gains a calling convention but loses
none, and existing direct-call registrations keep working unchanged. The
`EventRuntime`/`event_runtime` names are internal (not part of this
project's public API surface per the existing `__all__` in
`django_queue/__init__.py`), so the rename is not a breaking change for
consumers of the package.

## Open Questions

None. The prior version of this design deferred whether `EventRuntime` and
`AsyncQueueRuntime` should later be unified; this revision resolves that
question by unifying them now, in this change, rather than deferring it.
