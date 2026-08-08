## Why

Instants are represented three ways: entries persist ISO strings, hold `datetime`
in memory, and a worker times itself on local UTC. None matches the
authoritative source, since Redis `TIME` reports epoch seconds and microseconds
as integers. Before any of that can be unified, the system needs one type that
says what an instant is.

## What Changes

- Add a `ClockTime` value: an immutable instant holding whole seconds and
  microseconds since the Unix epoch, rejecting components that cannot describe
  one.
- Construct it from a float count of seconds, from the integer second and
  microsecond pair a Redis `TIME` reply yields, and from a timezone-aware
  datetime.
- Convert out of it to a float count of seconds and to a datetime.
- Order instants, measure the seconds between two of them, and shift one by a
  count of seconds, while rejecting the addition of two instants.

## Capabilities

### New Capabilities

- `clock-time`: Represent an instant as an exact, ordered, immutable value, and
  convert between it and the forms its sources and consumers use.

### Modified Capabilities

None. Nothing adopts the type in this change; `adopt-clock-time` does that.

## Impact

Adds one public value type to `django_queue.clock`. Nothing else changes, so
this change is inert until its adopter lands.

This package has no released consumers and no stored entries to preserve. There
is no legacy behaviour, no migration, and no wire compatibility to maintain.
