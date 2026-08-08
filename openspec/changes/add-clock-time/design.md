## Context

`django_queue.clock` defines the clock protocol and its local and Redis-aligned
implementations, all of which report a `datetime`. Entries store ISO strings and
hold `datetime`. Redis `TIME`, the authoritative source for Redis-backed queues,
reports neither: it returns epoch seconds and microseconds as integers.

This change introduces the type those will converge on. It adopts it nowhere.

## Goals / Non-Goals

**Goals:**

- Give an instant one exact, immutable representation with a name.
- Make an instant constructible from every form the system currently receives
  one in, and convertible to the forms its consumers need.
- Support the arithmetic that instants genuinely have, and refuse the arithmetic
  they do not.

**Non-Goals:**

- Adopting the type anywhere. The clock protocol, entry records, worker
  snapshots and the durable representation are `adopt-clock-time`.
- Representing durations. Budgets, grace periods, offsets and intervals stay
  plain counts of seconds.
- Calendar arithmetic, formatting, parsing or zone handling; a caller wanting
  any of that converts to a `datetime` and uses the standard library.

## Decisions

### A value type, not an alias for `float`

An alias is transparent: a checker cannot tell `ClockTime = float` from a
duration or from any other number, so it documents an intent it cannot enforce.
A distinct type is checked, which is the whole reason to name the concept.

It is also what lets durations stay plain second counts. The execution budget,
the grace period, the refresh interval and the clock offset are not instants,
and with `ClockTime` nominal a checker rejects mixing them without a second type
being introduced to say so.

### Whole seconds and microseconds, held separately

Storing the two components as integers makes the value exact by construction,
with no question of representation error, and mirrors the shape Redis `TIME`
already returns so that reading one is a construction rather than a conversion.

It also gives validation somewhere to live: a microsecond component outside
`[0, 1_000_000)` or a component that is not a whole number cannot describe an
instant, and is rejected when the value is built rather than left for every
consumer to re-check. An alias can express none of that.

Ordering follows from the component order and needs nothing written: microseconds
never reach a second, so comparing seconds then microseconds is chronological.
Expiring claims and retention sweeps both need instants to order.

### Construction is named, conversion is explicit

Each source gets a named constructor rather than an overloaded initialiser, so a
call site says which form it started from: `from_timestamp` for a float,
`from_timeval` for the integer pair, `from_datetime` for an aware datetime. An
aware datetime is required, because a naive one does not identify an instant.

Conversion out is `float()` for the durable and numeric forms, and
`to_datetime()` for callers that want calendar behaviour. There is no
ISO-string constructor: nothing in the system produces one, and adding it before
something does would be speculative.

Python performs no implicit conversion — `json.dumps` ignores `__float__` and
raises — so a consumer that needs a number asks for one. That is deliberate: the
durable form should be chosen by whoever owns the wire, not inherited by
accident.

### Arithmetic is direct, and only where it means something

The supported operations are the ones instants genuinely have, so callers write
ordinary expressions rather than unwrapping to floats first:

- instant minus instant yields the seconds between them
- instant plus or minus a count of seconds yields another instant
- instants compare and order against each other

Adding two instants is meaningless and is not supported, so a checker reports it
rather than letting it produce a value that looks like a time.

The Redis clock's calibration is the case that shapes this. Its offset is a
duration, and expressing it needs both operations and no wrapper: the offset is
the Redis instant minus the local instant, the reported time is the local instant
plus that offset, and the drift guard compares the offset against a plain second
count.

## Risks / Trade-offs

- [A value type must be converted at every boundary that wants a number] →
  Accepted, and the reason the conversions are named and explicit. The
  alternative, a transparent alias, removes the conversions by removing the
  guarantee that made the type worth adding.
- [Two representations exist until the adopter lands] → This change is inert on
  its own: nothing constructs a `ClockTime` until `adopt-clock-time`. The pair
  should land together even though they are separately reviewable.
- [Microsecond resolution is fixed by the component pair] → It matches what
  Redis reports and what `datetime` resolves to, so nothing in the system can
  currently express anything finer.
