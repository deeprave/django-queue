## Why

Operators need to identify live workers and diagnose queue handling without
coupling generic queues to a monitoring vendor.

## What Changes

- Add worker IDs and lifecycle/dispatch counters.
- Expose lightweight worker health snapshots.
- Emit structured lifecycle and dispatch log records derived from those
  snapshots.

## Capabilities

### New Capabilities
- `worker-observability`: Identify workers and expose local worker health and counters.

### Modified Capabilities
None.

## Impact

Adds worker metadata, APIs, and structured logging; no delivery guarantee
changes.
