## Why

Completed entry records otherwise persist indefinitely and make status storage
unbounded.

## What Changes

- Add configurable retention for terminal entry records.
- Provide explicit cleanup that emits an AsyncQueue observer event before a
  retained record is removed.

## Capabilities

### New Capabilities
- `entry-retention`: Retain terminal entries for a configured period then remove them.

### Modified Capabilities
None.

## Impact

Adds queue configuration, observable cleanup behavior, and an observer-only
termination event without changing pending work or durable queue states.
