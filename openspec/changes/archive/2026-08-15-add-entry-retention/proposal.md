## Why

Completed entry records otherwise persist indefinitely and make status storage
unbounded.

## What Changes

- Add configurable retention for terminal entry records.
- Permit a queued entry to transition directly to `failed` when it cannot reach
  handler dispatch, while keeping `succeeded` limited to entries that ran.
- Provide both scheduled cleanup and explicit `prune_entry` / `aprune_entry`
  cleanup for terminal records; both remove the durable record before
  best-effort publication of an observer-only AsyncQueue termination snapshot.
- Raise `QueueEntryNotFoundError` when an identified retained entry does not
  exist, rather than treating it as an empty queue.

## Capabilities

### New Capabilities
- `entry-retention`: Retain terminal entries for a configured period then remove them.

### Modified Capabilities
- `queue-entries`: Extend lifecycle transitions with pre-dispatch failure and
  observer-only termination.
- `async-queue-backends`: Add retained-entry lookup, explicit pruning, and
  scheduled retention cleanup.

## Impact

Adds queue configuration, pre-dispatch failure and explicit-pruning APIs,
observable cleanup behavior, a dedicated missing-entry exception, and a final
`terminated` lifecycle state that is published but never retained.
