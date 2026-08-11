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

### It lives in `django_queue.clock`, and is exported from the package root

Beside the protocol that produces it. That module imports only the standard
library — `logging`, `time`, `datetime`, `threading`, `typing` — and nothing from
the package, so a type declared there can be imported by entries, backends and
the worker without any of them creating a cycle. A separate module would buy
nothing and put the type somewhere other than where a reader looking for time
handling would go.

`ClockTime` is also added to `django_queue.__all__`. Once `adopt-clock-time`
lands, every entry a consumer reads carries three of them, so it is public API
whether or not it is exported; naming it there says so, and saves consumers
importing from a submodule. The clock protocol and its implementations stay
where they are — the value is what consumers handle, the clocks are what the
package wires up.

### Whole seconds and microseconds, held separately

Storing the two components as integers makes the value exact by construction,
with no question of representation error, and mirrors the shape Redis `TIME`
already returns so that reading one is a construction rather than a conversion.

It also gives validation somewhere to live: a component that is not a whole
number, a microsecond component outside `[0, 1_000_000)`, and a negative second
component are each rejected when the value is built rather than left for every
consumer to re-check. An alias can express none of that.

Seconds are non-negative, so the epoch is the earliest instant representable and
`from_timestamp` rejects a negative argument. Nothing in the queue produces a
pre-epoch time — Redis `TIME` cannot, and a queue timestamps its own present.
Excluding them also removes the only case where the component pair is subtle: a
negative float would have to floor rather than truncate to keep microseconds in
range, a rule with no reader and no caller.

Ordering follows from the component order and needs nothing written: microseconds
never reach a second, so comparing seconds then microseconds is chronological.
Expiring claims and retention sweeps both need instants to order.

Non-negative seconds also make the epoch a floor on arithmetic, not only on
construction: subtracting a duration larger than the elapsed time since 1970
fails rather than producing a pre-epoch value. The rule is that every operation
yielding an instant yields a valid one, and validity is decided in one place. It
is unreachable with any time the system actually handles — it needs a duration
of more than fifty-five years — but it is stated so it is not later mistaken for
a defect.

### Validation raises the standard exceptions

A component of the wrong type raises `TypeError`; a component of the right type
that cannot describe an instant — a microsecond out of range, a second before
the epoch, a naive datetime, a count of seconds that is NaN or infinite —
raises `ValueError`. That is the ordinary Python split, and it is what Ruff's
`TRY004` expects, so the checks stay plain guards with nothing suppressed and no
conversion machinery between the check and the raise.

The type check is `type(value) is int` rather than `isinstance`, because `bool`
subclasses `int` and a frozen dataclass stores whatever it is handed: an
`isinstance` guard admits `ClockTime(True, False)`, which then holds actual
`bool` objects in fields declared `int` and compares equal to `ClockTime(1, 0)`,
so no value-comparing test can see it. A flag standing in for a component is
precisely the confusion this type exists to catch, so it is rejected even though
a bool is technically a whole number.

Non-finite floats are rejected up front rather than left to `int()` and
`round()`, which would otherwise raise the standard library's own wording and,
for infinity, an `OverflowError` outside the pair documented above. NaN is the
case that forces the explicit guard: it compares false against everything, so a
`timestamp < 0` test cannot catch it.

`django_queue.backends.exceptions` defines `QueueValueError(QueueException,
ValueError)`, currently declared and exported but raised nowhere. It is tempting
here and deliberately not used.

An instant is a value, not a queue operation; a microsecond component out of
range is a bad argument, not a queue failure, and nothing catching
`QueueException` would want it. Using it would also couple `clock.py` to
`django_queue.backends`, and so to Django, against the isolation decided above.
That import happens to work today only because `backends/__init__.py` imports
`.exceptions` on its first line, before `.memory`, which imports `clock` — a
guarantee held up by line ordering in another module, which reordering would
break silently.

Moving the exception hierarchy to a package-level `django_queue/exceptions.py`
would resolve the tension properly and give `QueueValueError` a home a value type
could reach. That is worth doing on its own terms and is not done here.

### Construction is named, conversion is explicit

Each source gets a named constructor rather than an overloaded initialiser, so a
call site says which form it started from: `from_timestamp` for a float,
`from_timeval` for the integer pair, `from_datetime` for an aware datetime. An
aware datetime is required, because a naive one does not identify an instant.

Conversion out mirrors that: `to_timestamp()` for the durable and numeric forms,
`to_datetime()` for callers that want calendar behaviour. Naming it against
`from_timestamp` makes the round trip read as one, and keeps each direction a
call the reader can see. There is no ISO-string constructor: nothing in the
system produces one, and adding it before something does would be speculative.

`from_datetime` reads through a `timedelta` against a UTC epoch constant rather
than through `datetime.timestamp()`. Both a `datetime` and a `ClockTime` hold
exact integer microseconds, so routing between them through a float would
discard precision that neither side lacks — measurably so beyond 2^33 seconds,
where a double can no longer resolve a microsecond. That limit is a property of
the durable form, chosen deliberately below; there is no reason for a
constructor to inherit it.

`__float__` is deliberately not implemented. It would make an instant acceptable
anywhere a number is, which is precisely where a duration is usually wanted —
`asyncio.timeout`, a `TIMEOUT` setting, the drift comparison — and the type
exists to keep those apart. Without it there is no coercion path at all:
`json.dumps` raises on a `ClockTime` rather than emitting a number, so the
durable form is chosen by whoever owns the wire rather than inherited by
accident.

### Arithmetic is direct, and only where it means something

The supported operations are the ones instants genuinely have, so callers write
ordinary expressions rather than unwrapping to floats first:

- instant minus instant yields the seconds between them
- instant plus or minus a count of seconds yields another instant
- instants compare and order against each other

Addition takes the duration on either side, via `__radd__` delegating to
`__add__`. Nothing distinguishes `grace + started` from `started + grace` in
meaning, so a directional restriction would be an accident of implementation
rather than a decision, and would fail an ordinary spelling for no reason.
Subtraction stays asymmetric, since a duration minus an instant means nothing.

Adding two instants is meaningless and is not supported, so a checker reports it
rather than letting it produce a value that looks like a time.

The Redis clock's calibration is the case that shapes this. Its offset is a
duration, and expressing it needs both operations and no wrapper: the offset is
the Redis instant minus the local instant, the reported time is the local instant
plus that offset, and the drift guard compares the offset against a plain second
count.

### The `no-matching-overload` suppression goes with it

`pyproject.toml` disables `no-matching-overload` across the package. Its comment
blames redis-py typing command arguments narrowly, but the cause is a wide
return: `zrevrange` is not overloaded on the `withscores` literal, so even with
`withscores=False` it yields
`list[bytes | str] | list[tuple[bytes | str, Any]] | list[list[Any]]`, and the
`item[0]` handed to `zrem` in the priority queue's `get` infers as
`bytes | str | tuple[bytes | str, Any] | list[Any]`. The `tuple` and `list`
members, which only arise under `withscores=True`, are what fail to match.

It is one call site. Narrowing the member before the `zrem`, and raising
`QueueEncodingException` otherwise, clears the diagnostic with the rule set to
`error` — the same shape as the `_decode` narrowing, and no `Any`. The narrowed
value goes to `zrem` directly rather than a decoded round trip, because with a
bytes client the member must match the stored bytes exactly and `self._encoding`
need not be the connection's.

The accepted set is `bytes | bytearray | memoryview | str`, matching what
`_decode` handles and what `zrem`'s own signature takes. A tighter `bytes | str`
would satisfy the checker equally, but it would make `get` reject values that
`peek` — which routes the same reply straight to `_decode` — accepts without
complaint, so the two methods would disagree about one Redis reply.

This is not strictly this change's business, but a package-wide suppression of
an overload-resolution diagnostic is the wrong thing to be carrying while
introducing a type whose arithmetic is defined by overloads and whose correctness
task asks `ty` to prove. The unsupported two-instant addition reports as
`unsupported-operator` rather than `no-matching-overload`, so it is not masked
today — but the guarantee is accidental, and removing the rule makes it real.

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
- [The `zrem` narrowing is out of scope for a value type] → Accepted
  deliberately. It is a few lines in one backend, it leaves the package in a
  better state than it found it, and it is what makes this change's own
  overload-checking task meaningful rather than assumed.
- [The narrowing raises on a branch that cannot occur] → `withscores=False`
  guarantees a flat list, so the guard is an assertion the stub cannot make. It
  raises `QueueEncodingException`, consistent with `_decode`, rather than
  silently passing a value Redis would reject.
