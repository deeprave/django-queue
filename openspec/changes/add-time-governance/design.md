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
  budget measured from dispatch.
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

### One time representation everywhere: float epoch seconds

Entries currently persist `queued_at`, `dispatched_at` and `finished_at` as ISO
strings while holding `datetime` in memory, and a worker times itself on local
UTC. Three representations and two conversion boundaries, none of which the
authoritative source uses: Redis `TIME` returns epoch seconds and microseconds
as integers.

Every instant in the system becomes a float count of seconds since the Unix
epoch — the representation Python itself uses for `time.time()` and
`datetime.timestamp()`. Not just on the wire: the clock protocol returns it,
entries hold it, the worker snapshot reports it, structured logs carry it, and
it is what the public API hands back.

The point is the consistency rather than the format. A single representation end
to end means no conversion boundary to get wrong, and conversion boundaries are
where this codebase has already produced defects. Epoch is absolute, so there is
no zone to record or resolve. The value is numerically ordered, which an ISO
string is not usefully: expiring claims and retention sweeps need to compare
stored times against a bound, and Redis sorted-set scores are themselves IEEE
doubles, so a stored float is a sort score with no conversion at all.

Precision is adequate and was measured, not assumed. Adjacent doubles near the
present are about 238 nanoseconds apart, comfortably finer than the microsecond
resolution Redis reports, and `datetime.timestamp()` round-tripped 200,000
random microsecond values without a single mismatch.

Callers wanting a `datetime` or a formatted string convert at the edge —
`datetime.fromtimestamp(entry.queued_at, UTC)` — which is deliberately their
concern, not a second internal representation.

Integer epoch microseconds were considered. They are exactly representable and
avoid float equality, but they are not what Python's own time API returns, they
would need a unit-bearing field name to be unambiguous, and they buy nothing
here that the measured precision does not already provide.

### The representation is a named type, not a bare float

`float` says nothing about what a value means. An instant, a duration, and a
drift tolerance are all floats, and once they are all bare floats nothing stops
one being passed where another belongs.

A `ClockTime` alias is declared in `django_queue.clock`, beside the protocol that
produces it, and annotates every instant in the system: what a clock returns, the
entry's lifecycle fields, the worker's run start time, and anything a snapshot or
log record reports. Declaring it once gives the meaning a single home to be
documented and changed in, and makes an instant recognisable at every use site
without tracing back to where the value came from.

It is deliberately not applied to durations. The execution budget, the
cancellation grace period, and the clock's refresh interval and drift tolerance
are all counts of seconds rather than points in time, and giving them the same
name as an instant would defeat the distinction the type exists to draw.

The alias is documentation for readers and type checkers rather than a runtime
guard, which is what is wanted here: clarity and consistency, without wrapping
every timestamp in an object the queue would then have to serialise.

### A worker times itself on its queue's clock

`add-worker-observability` sources the worker's run start time from local UTC
while entries are timestamped by their queue's clock, on the grounds that a
generic worker may serve several queues with independent clocks. That premise
does not hold: the package builds every worker through `BaseQueue.create_worker`
with a single alias, so a worker always has exactly one queue and one clock
available to it.

The variance has a cost. `RedisQueueClock` accepts up to 180 seconds of
Redis-to-local skew before it refuses to calibrate, so any elapsed time spanning
the two bases — how long after starting a worker picked up its first entry, for
one — can be wrong by that much, and an entry's `dispatched_at` can precede the
`started_at` of the very worker that dispatched it.

A worker therefore takes a clock, defaulting to `LocalQueueClock`, and
`create_worker` supplies its queue's. `BaseQueue` gains a public `clock`
accessor over the private attribute both backends already hold, mirroring
`queue_name`. A caller constructing a worker across several queues still chooses
the basis explicitly rather than silently getting a third one.

This governs recorded timestamps only. The execution budget is enforced on the
event loop's monotonic clock and is unaffected by wall-clock skew.

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
- [Reversing the observability change's local-UTC decision] → That change
  archives first, so this one modifies its requirement rather than contradicting
  it. Its design records the original reasoning and is left as the record of what
  was decided then; the correction belongs here, where the premise is re-examined.
