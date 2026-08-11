## Context

Three representations of an instant coexist. Entries persist ISO strings and
hold `datetime`. Clocks report `datetime`, the Redis-aligned one by converting
the integer pair `TIME` gives it. The worker's run start time is local UTC,
recorded through `datetime.now`.

`add-clock-time` defines the type these converge on. This change adopts it, and
in doing so corrects where the worker's own time comes from.

## Goals / Non-Goals

**Goals:**

- Use one representation for every instant the system reports or stores.
- Remove the string form entirely, and with it the parsing and zone handling
  each read performs.
- Give a worker's recorded time the same basis as the entries it dispatches.

**Non-Goals:**

- Changing what is timestamped, or when. The lifecycle fields and the moments
  they are set are unchanged.
- Changing the Redis clock's calibration policy: the refresh interval, the drift
  tolerance and the failure behaviour all stay as specified.
- Representing durations. Budgets, grace periods, offsets and intervals remain
  plain counts of seconds.

## Decisions

### Instants are stored as a float count of seconds

`ClockTime` does not serialise itself, and it implements no numeric coercion, so
`json.dumps` raises on one rather than emitting a number. The durable form is
therefore chosen rather than inherited, and the entry's conversion is explicit
in both directions: `to_timestamp` on the way out, `from_timestamp` on the way
back.

That form is a float count of seconds since the epoch. Epoch is absolute, so
there is no zone to record or resolve, and the value is numerically ordered
where an ISO string is not usefully: Redis sorted-set scores are themselves IEEE
doubles, so a stored instant is a sort score with no conversion at all, which
expiring claims and retention sweeps both need.

Storing the second and microsecond pair instead would be exact by construction,
but it is a JSON array, which cannot be a score or a range bound without being
taken apart again. Nothing is lost by the float for any instant a queue can
observe: adjacent doubles near the present are about 238 nanoseconds apart,
comfortably finer than the microsecond resolution Redis reports, and the round
trip is exact across all one million microsecond values at the current epoch.

The margin is finite. A double's spacing crosses one microsecond between 2^32
and 2^33 seconds — measured, the round trip is exact at 2^32 (year 2106) and
loses microseconds for roughly half of sampled values at 2^33 (year 2242). That
is the bound on the durable form, and `add-clock-time` states it on the
requirement rather than leaving an unqualified guarantee. It does not change the
choice made here: two centuries of headroom is ample, and the alternatives were
rejected for reasons that have nothing to do with resolution.

This does introduce a conversion at the wire boundary. It is accepted because it
is one typed conversion in one place, tested, replacing the
`isoformat`/`fromisoformat` pairs currently spread across the backends.

### The Redis clock stops passing through a datetime

`TIME` reports whole seconds and microseconds. Today those are assembled into a
`datetime`, offset with a `timedelta`, and later rendered to a string. With an
instant type the calibration is expressed directly: the offset is the Redis
instant minus the local instant, a count of seconds; the reported time is the
local instant plus that offset; and the drift guard compares that count against
its threshold.

Nothing about the calibration policy changes — one refresh per interval, the
last good offset retained on failure, a clear failure when initial calibration
cannot be trusted. Only its arithmetic gets shorter.

### A worker times itself on its queue's clock

`add-worker-observability` sources the run start time from local UTC while
entries are timestamped by the queue's clock, on the grounds that a generic
worker may serve several queues with independent clocks. That premise does not
hold: the package builds every worker through `BaseQueue.create_worker` with a
single alias, so a worker always has exactly one queue and one clock available
to it.

The variance has a cost. The Redis clock accepts up to 180 seconds of skew
before it refuses to calibrate, so any elapsed time spanning the two bases can
be wrong by that much, and an entry's `dispatched_at` can precede the run start
time of the very worker that dispatched it.

A worker therefore takes a clock, defaulting to local time, and `create_worker`
supplies its queue's. `BaseQueue` gains a public `clock` accessor over the
private attribute both backends already hold, mirroring `queue_name`. A caller
constructing a worker across several queues still chooses the basis explicitly
rather than silently getting a third one.

That change's design records the original reasoning and is left as the record of
what was decided then; the correction belongs here, where the premise is
re-examined.

### Durations are derived, never stored

`queued_for` and `ran_for` are read-only properties over the instants the entry
already holds, and a snapshot's `running_for` is computed when the snapshot is
taken. Nothing new enters the durable record.

Storing them would duplicate derivable state, which is the one way a record can
contradict itself: an entry rewritten at each transition could carry a
`ran_for` that disagrees with its own `dispatched_at` and `finished_at`, and
nothing could say which is right. Deriving also means every entry already
written gains the durations without migration, and it keeps the wire format
exactly as the storage decision above settled it.

A duration the instants cannot yet describe is absent rather than zero. An entry
that has not been dispatched has not waited zero seconds — the question has no
answer yet, and zero is an answer.

Instants that contradict each other are treated the same way. A Redis-aligned
clock is recalibrated periodically and its offset may move backwards within the
drift tolerance, so an instant read after a refresh can precede one read before
it, and a subtraction would yield a negative duration. That is not a smaller
elapsed time but a meaningless one, and it would be published straight into
snapshots and structured logs. Reporting it as absent is the same answer already
given when the instants cannot describe a duration, so a consumer needs one rule
rather than two.

Clamping to zero was rejected: it produces a plausible number from contradictory
inputs, which is worse than an obvious gap. Both cases route through one helper,
so an entry and a worker cannot disagree about what an unanswerable duration
looks like.

A worker's `running_for` is measured against its own clock at snapshot time, so
a reader needs no second source of time to interpret it, and the number cannot
disagree with the `started_at` beside it. Once the loop exits the worker records
a stop instant and measures against that, because a stopped worker that reports
an ever-growing runtime is simply wrong.

### Elapsed time is for reporting, not for enforcing a budget

This is the distinction `add-timeout-governance` depends on and must not lose.
An execution budget is enforced on the event loop's monotonic clock, because a
wall clock can jump and this one is periodically recalibrated against Redis — a
handler must not be killed early because an offset moved beneath it.

The durations here are the wall-clock record of what happened, which is a
different job: how long a handler ran before it was abandoned, how long work sat
before pickup. They are also the only basis available to the changes that reason
across processes — expiring another worker's claim, or sweeping entries finished
more than some interval ago — since a monotonic clock means nothing outside the
process that read it.

## Risks / Trade-offs

- [Every instant in the public API changes type at once] → Unavoidable for a
  representation change, and the reason it is separated from the timeout work:
  this change alters how time is expressed and nothing about what the queue
  does.
- [The durable form changes from a string to a number] → No stored entries
  exist to reinterpret, and `from_dict` continues to reject anything it does not
  recognise rather than guessing.
- [A conversion boundary returns at the wire] → One typed conversion in one
  place, replacing several untyped ones; see the storage decision above.
