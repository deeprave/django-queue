## Why

Claimed entries must become available again when their worker dies, completing
the path to Redis at-least-once delivery.

## What Changes

- Add expiring claim leases and recovery of expired claims.
- Update worker dispatch to atomically settle terminal outcomes with their claims.

## Capabilities

### New Capabilities
- `redis-claim-leases`: Recover expired Redis claims and provide at-least-once delivery.

### Modified Capabilities
- `queue-entries`: Permit reliable-delivery recovery to return an abandoned
  running entry to queued work before retrying it.

## Impact

Depends on `add-redis-claim-ack`; changes Redis worker delivery semantics.
