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

`ClockTime` does not serialise itself, and Python performs no implicit
conversion — `json.dumps` ignores `__float__` and raises — so the durable form
is chosen rather than inherited, and the entry's conversion is explicit in both
directions.

That form is a float count of seconds since the epoch. Epoch is absolute, so
there is no zone to record or resolve, and the value is numerically ordered
where an ISO string is not usefully: Redis sorted-set scores are themselves IEEE
doubles, so a stored instant is a sort score with no conversion at all, which
expiring claims and retention sweeps both need.

Storing the second and microsecond pair instead would be exact by construction,
but it is a JSON array, which cannot be a score or a range bound without being
taken apart again. Nothing is lost by the float: adjacent doubles near the
present are about 238 nanoseconds apart, comfortably finer than the microsecond
resolution Redis reports, and the round trip is exact across the microsecond
range.

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
