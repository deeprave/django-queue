## Why

Completed entry records otherwise persist indefinitely and make status storage
unbounded.

## What Changes

- Add configurable retention for terminal entry records.
- Provide explicit cleanup for backends that cannot expire records natively.

## Capabilities

### New Capabilities
- `entry-retention`: Retain terminal entries for a configured period then remove them.

### Modified Capabilities
None.

## Impact

Adds queue configuration and cleanup behavior without changing pending work.
