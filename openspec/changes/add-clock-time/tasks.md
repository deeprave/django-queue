## 1. The instant value

- [ ] 1.1 Add failing tests for construction and validation: the same moment
  built from a float, from a second and microsecond pair, and from an aware
  datetime compares equal; a naive datetime, an out-of-range microsecond
  component, and a fractional component are each rejected.
- [ ] 1.2 Implement `ClockTime` in `django_queue.clock` as a frozen, ordered
  value holding whole seconds and microseconds, with `from_timestamp`,
  `from_timeval` and `from_datetime`, validating on construction.

## 2. Conversion and arithmetic

- [ ] 2.1 Add failing tests that an instant converts to a float and back to an
  equal instant across the microsecond range, and converts to an aware datetime
  describing the same moment.
- [ ] 2.2 Implement `__float__` and `to_datetime`.
- [ ] 2.3 Add failing tests for ordering within and across seconds, for elapsed
  time between two instants, and for shifting an instant by a count of seconds.
- [ ] 2.4 Implement the arithmetic with overloads so each result type is
  checked, leaving the addition of two instants unsupported.
- [ ] 2.5 Confirm `ty` reports adding two instants as an error rather than
  accepting it, and that elapsed time is typed as a number of seconds while a
  shift is typed as an instant.

## 3. Documentation and validation

- [ ] 3.1 Document the value in the README: what it represents, its
  constructors, how to reach a float or a datetime from one, and that durations
  remain plain counts of seconds.
- [ ] 3.2 Run Ruff, ty, the full pytest suite, and strict OpenSpec validation.
