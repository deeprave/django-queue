## Why

`EventQueueWorker._invoke` (`django_queue/event_worker.py:172-178`) dispatches
a `queue_listener` callback differently by sync/async shape:

```python
async def _invoke(self, callback: Any, entry: QueueEntry) -> Any:
    if inspect.iscoroutinefunction(callback):
        return await callback(entry)
    result = await sync_to_async(callback)(entry)
    if inspect.isawaitable(result):
        return await result
    return result
```

A *sync* callback runs through `sync_to_async`'s thread-pool executor and is
correctly isolated from the event loop. An *async* callback runs directly,
inline, awaited straight through `_dispatch` on `EventRuntime`'s one shared
loop, with no isolation at all. A slow-running async listener — a blocking
call disguised inside `async def`, or just legitimately long-running async
work — stalls that shared loop for as long as it runs, delaying every other
configured `EventQueue` alias's worker sharing it. This is present in the
codebase today, independent of any other in-flight change.

It surfaced during discussion of the (separate, in-progress)
`add-async-runtime` change, which unifies `EventRuntime` and a new
per-alias observer-receiver task onto one shared loop
(`QueueRuntime`). The discussion first considered whether the observer
*receiver* itself (`aobserve()`, a `redis.asyncio` pubsub listen loop) posed
a stalling risk on that shared loop, and concluded the exposure there is
narrow — `aobserve()`'s own task body does no blocking work and is a
well-behaved async generator, and user observer callbacks already run
isolated on `_QueueObservers`'s own dedicated dispatcher thread, unchanged
by that design. The sharper, correctly-identified concern was the event
*listener* side instead: `EventQueueWorker._invoke`'s inline async-callback
dispatch has no equivalent isolation, and — once `add-async-runtime` lands
and both task kinds share one loop — a slow async listener would newly also
delay `AsyncQueue` lifecycle-observer delivery, not just other event-queue
workers. `add-async-runtime`'s design.md documents this as a known,
pre-existing, explicitly out-of-scope limitation for that change, with a
note pointing here.

This change exists to investigate that gap properly — understand its actual
severity and trigger conditions, survey what isolation options exist and
their trade-offs, and decide on (and specify) a fix — rather than settle for
either living with it indefinitely or bolting on an under-considered patch
as a rushed side effect of an unrelated change.

## What Changes

This proposal opens an investigation; it does not yet commit to a specific
fix. Expected shape of the work, to be refined during design:

- Characterise the actual risk: what does "slow" mean in practice for an
  async listener (a blocking call with no `await`, vs. legitimately
  long-running awaited work); how long can a listener realistically run
  before delaying other workers becomes user-visible; whether any existing
  test or production usage already exercises this path.
- Survey isolation approaches for async listener dispatch — candidates
  raised in the originating discussion include a bounded `asyncio.wait_for`
  timeout around the callback, dispatching via a supervised separate task
  rather than an inline `await`, and a documented timeout convention for
  listener authors — and evaluate trade-offs (added complexity, behavior
  change for existing async listeners, whether a default timeout is even
  a sound default for arbitrary user code).
- Decide whether the fix belongs in `EventQueueWorker._invoke` uniformly,
  or is opt-in/configurable per listener registration.
- Specify the chosen approach's effect on existing `event-queue-listeners`
  dispatch guarantees (ordering, retry-on-exception, lease renewal during a
  long-running listener) so nothing already specified regresses.

## Capabilities

### New Capabilities
None anticipated — this is dispatch-isolation behavior within the existing
event-listener capability, not a new one. If design work concludes a new
concept (e.g. a listener timeout setting) needs its own spec section, revise
this before writing specs.

### Modified Capabilities
- `event-queue-listeners`: dispatch behavior for async listener callbacks
  changes (isolation from the shared runtime loop), pending the specific
  approach chosen during design. The exact requirement text depends on that
  decision and will be drafted once design.md settles the approach — this
  proposal names the capability now so specs work has a home once decided.

## Impact

- `django_queue/event_worker.py`: `EventQueueWorker._invoke`, and possibly
  `_dispatch`/`_renew_claim` if a chosen approach affects lease-renewal
  timing during a long-running listener.
- `django_queue/event_runtime.py` (or `django_queue/queue_runtime.py`, if
  `add-async-runtime` has landed by the time this is implemented): the
  shared loop whose stability motivates this change, though this change is
  not expected to modify that file directly.
- `openspec/specs/event-queue-listeners/spec.md`: pending design decision.
- Related, not a dependency: `openspec/changes/add-async-runtime/` (its
  design.md documents this gap and points here; it does not block starting
  this exploration, since the gap already exists independent of that
  change landing).
- Existing tests in `tests/test_event_listeners.py`/`tests/test_event_worker.py`
  covering async listener dispatch will need review once an approach is
  chosen, to confirm they still hold and to add coverage for the new
  isolation behavior.
