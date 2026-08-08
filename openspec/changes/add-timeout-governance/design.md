## Context

`AsyncQueueWorker._dispatch` awaits its handler with no deadline. Dispatch is
sequential per alias, so one handler that never returns holds its entry at
`running` and starves the alias for the life of the process.

The only existing deadline is `cancellation_grace_period`, reached solely from
the shutdown path. Its expiry calls `mark_cancelled`, so a handler that ignored
cancellation and one that stopped cleanly produce the same terminal status and
the same `cancelled_count`.

This package has no released consumers and no stored entries. Every artefact
below is new code.

## Goals / Non-Goals

**Goals:**

- Bound handler execution so a hung handler cannot starve its queue.
- Distinguish abandoned-on-timeout from cancelled-on-shutdown in the entry
  status, the worker counters, and the structured logs.
- Let a caller set a budget when enqueueing work, a queue supply a default, and
  a worker override both.
- Let a handler that is still working extend its own budget rather than be
  killed for taking a long time legitimately.

**Non-Goals:**

- Retry, redelivery, or rescheduling of timed-out entries; the entry reaches a
  terminal status and stops there.
- Bounding anything other than handler execution — backend calls, the activation
  poll, and the ASGI lifespan keep their current behaviour.
- Wall-clock deadlines or scheduling ("run before T"); this is an execution
  budget measured from dispatch on the event loop's monotonic clock, so it is
  unaffected by wall-clock skew and by how instants are represented.
- Changing how instants are represented or where a worker's recorded time comes
  from; `adopt-clock-time` settles both, and this change adds only durations.
- Cross-process enforcement. A worker enforces the budget for entries it is
  dispatching; recovering entries abandoned by a dead worker is
  `add-redis-lease-recovery`.

## Decisions

### No compatibility surface

There is nothing to preserve. The entry status set gains a member, the entry
record and its JSON representation gain a field, and `BaseQueue` gains an
abstract method. None of these is gated, defaulted for old readers, or
version-tolerant, and `QueueEntry.from_dict` continues to reject any status it
does not recognise. No migration, no upgrade ordering, no deprecation period.

### `timeout` is a terminal status, not a failure subtype

Recording expiry as `failed` with a `TimeoutError` payload would work, but it
conflates a handler that raised with one that never answered — the distinction
an operator acts on. `timeout` joins `succeeded`, `failed`, and `cancelled` as a
terminal state reachable only from `running`, with a matching `mark_timed_out`
on the backend contract and a `timed_out_count` on the worker snapshot.

Shutdown grace-period expiry routes here too. After this change `cancelled`
means only that the worker stopped the entry deliberately and the handler
complied.

### Budget resolution: worker, then entry, then queue, then 600 seconds

The worker override wins over the value carried on the entry, which wins over
the queue default, which falls back to 600 seconds. A worker is the component
that knows the runtime it is actually operating in, so it takes precedence over
what a producer asked for. A resolved budget is always a positive number of
seconds; there is no "unlimited" value, because an unbounded handler is the
defect this change exists to remove.

The budget is carried on the entry so it survives enqueue, is visible in the
durable record, and is available to whichever worker dispatches it.

### The budget applies to entry dispatch, not to raw items

`enqueue` creates an identified entry and gains a `timeout` keyword. `add` is
the item-oriented API: it stores raw values, creates no entry, and is never
dispatched to a handler, so it has nothing to which a budget could apply and
gains no keyword.

### `asyncio.timeout` provides the extendable deadline

`_dispatch` wraps the handler await in `asyncio.timeout(budget)`. Extension uses
the context manager's `reschedule()`, which is what it exists for — `wait_for`
cannot move its deadline once started. On expiry the handler task is cancelled
and the entry is marked timed out, mirroring how grace-period expiry already
handles a handler that will not stop.

### The heartbeat is a module-level call, not a handler argument

A handler is `Callable[[QueueEntry], Awaitable[object]]`. Threading a heartbeat
object through that signature would change the handler contract and force it on
handlers that never ping. Instead the worker publishes the active dispatch's
extension callback in a `ContextVar`, and a public `heartbeat()` reads it:

```python
async def process(entry):
    for chunk in entry.payload["chunks"]:
        await handle(chunk)
        heartbeat()
```

The handler task inherits the worker's context at creation, so the call works at
any depth, including inside `asyncio.to_thread` (which copies the context).
`heartbeat()` therefore reschedules through the loop rather than touching the
timer directly, so a call from a worker thread is safe. Called outside a
dispatch it raises rather than silently doing nothing.

Each heartbeat grants a fresh full budget from the moment of the call. A handler
that pings faster than its budget runs indefinitely, which is the intent: the
budget bounds silence, not total runtime.

## Risks / Trade-offs

- [A handler that pings while wedged never times out] → Accepted. The heartbeat
  is an assertion of progress; a caller that pings from a loop that is itself
  stuck has defeated it deliberately. The budget bounds unresponsiveness, and
  detecting dishonest progress is out of scope.
- [600 seconds is arbitrary and will be wrong for some queues] → It only applies
  when neither the entry, the queue, nor the worker specifies one; all three
  levels exist precisely so the default rarely governs.
- [Worker precedence surprises a producer that set a budget] → Documented as the
  resolution order, and the queue default covers the common case where no one
  sets anything. The alternative, most-specific-wins, would let a producer pin a
  budget the operator running the worker cannot correct.
- [Timing out a handler leaves its side effects half-applied] → Same exposure as
  the existing cancellation path; the entry records a terminal outcome and the
  handler's own idempotency remains its responsibility.
- [`mark_timed_out` is another backend-contract addition] → Unavoidable for a
  new terminal status, and there is no external backend to break.
