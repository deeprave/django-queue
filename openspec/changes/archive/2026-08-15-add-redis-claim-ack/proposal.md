## Why

Best-effort dequeue can lose work after a worker crashes. Reliable delivery
needs an explicit ownership boundary before lease recovery is added.

## What Changes

- Add atomic Redis claim and acknowledgement operations.
- Track claimed entries separately from pending entries.

## Capabilities

### New Capabilities
- `redis-entry-claims`: Atomically claim and acknowledge Redis queue entries.

### Modified Capabilities
None.

## Impact

Adds Redis-only reliable-delivery primitives; workers remain best effort until
lease recovery is implemented.
