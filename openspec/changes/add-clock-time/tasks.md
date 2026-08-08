## 1. Unmask overload diagnostics

Done first, so every type-checker assertion later in this change means what it
says rather than surviving a package-wide suppression.

- [x] 1.1 Add a failing test that the Redis priority queue's `get` returns and
  removes the stored member with `decode_responses` both on and off.
- [x] 1.2 Narrow `item[0]` to `bytes | str` before the `zrem` in
  `RedisPriorityQueue.get`, raising `QueueEncodingException` otherwise, and pass
  the narrowed member rather than a decoded round trip.
- [x] 1.3 Remove `no-matching-overload = "ignore"` and its comment from
  `pyproject.toml`, and confirm `ty` is clean across the package.

## 2. The instant value

- [x] 2.1 Add failing tests for construction and validation: the same moment
  built from a float, from a second and microsecond pair, and from an aware
  datetime compares equal; a naive datetime, an out-of-range microsecond
  component, and a negative second component or timestamp are each rejected
  with a `ValueError`, and a fractional component with a `TypeError`.
- [x] 2.2 Implement `ClockTime` in `django_queue.clock` as a frozen, ordered
  value holding whole seconds and microseconds, with `from_timestamp`,
  `from_timeval` and `from_datetime`, validating on construction. Keep the
  module free of package-internal imports so it stays importable from anywhere.
- [x] 2.3 Export `ClockTime` from `django_queue.__all__`.

## 3. Conversion and arithmetic

- [x] 3.1 Add failing tests that an instant converts to a count of seconds and
  back to an equal instant across the microsecond range, converts to an aware
  datetime describing the same moment, and raises rather than coercing when
  passed where a number is expected — including through `json.dumps`.
- [x] 3.2 Implement `to_timestamp` and `to_datetime`, and leave `__float__`
  unimplemented.
- [x] 3.3 Add failing tests for ordering within and across seconds, for elapsed
  time between two instants, for shifting an instant by a count of seconds, and
  for a shift that would land before the epoch being rejected.
- [x] 3.4 Implement the arithmetic with overloads so each result type is
  checked, leaving the addition of two instants unsupported.
- [x] 3.5 Confirm `ty` reports adding two instants as an error rather than
  accepting it, and that elapsed time is typed as a number of seconds while a
  shift is typed as an instant.

## 4. Review follow-ups

Raised by the collated review of this change and taken before merge.

- [x] 4.1 Add failing tests for a boolean component or timestamp, a non-finite
  timestamp or duration, addition with the duration on the left, and a datetime
  round trip at a magnitude a float cannot resolve.
- [x] 4.2 Reject booleans with `type(...) is int`, reject non-finite floats up
  front, add `__radd__`, and read `from_datetime` through a `timedelta` so it
  keeps the microseconds a datetime already holds.
- [x] 4.3 Widen the `zrem` guard to `bytes | bytearray | memoryview | str`, so
  `get` accepts what `_decode` and `zrem` accept and cannot disagree with
  `peek` about the same reply.
- [x] 4.4 Correct `get`'s docstring and comment, which said lowest priority
  where `zrevrange` returns the highest.
- [x] 4.5 Remove the three inert `# ty: ignore` comments from the tests, which
  suppressed nothing because ty checks only `django_queue`.
- [x] 4.6 Bound the round-trip requirement in the spec, and record the measured
  limit in this design and in `adopt-clock-time`'s storage decision.

## 5. Documentation and validation

- [x] 5.1 Document the value in the README: what it represents, its
  constructors, how to reach a count of seconds or a datetime from one, that it
  does not coerce to a number, and that durations remain plain counts of
  seconds.
- [x] 5.2 Run Ruff, ty, the full pytest suite, and strict OpenSpec validation.
