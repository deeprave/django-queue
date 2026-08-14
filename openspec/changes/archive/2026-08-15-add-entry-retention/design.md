## Context

Entry lookup is useful for status APIs but must not grow forever.

## Goals / Non-Goals

**Goals:** Bound terminal-record lifetime consistently across memory and Redis.

**Non-Goals:** Archival storage or retention of pending/running entries.

## Decisions

Use `RETENTION_TIMEOUT` for terminal-entry retention. It defaults to 600
seconds; explicit `None` disables automatic cleanup. A running worker performs
scheduled cleanup as part of its normal loop, while backends provide the shared
per-entry removal operation needed by both cleanup and explicit pruning.

### First-seen lifecycle observation

A running worker scans each AsyncQueue's retained entries at most once per
second. It publishes snapshots whose queue-owned UUIDv7 IDs are greater than
its completed-scan cursor, so entries transitioned outside the worker's own
dispatch path still become observable. The cursor advances only after the scan
completes; later dispatch skips queued publication for entries already covered
by that scan.

### One pruning operation, two triggers

`AsyncQueue` SHALL expose `prune_entry(entry_id)` and `aprune_entry(entry_id)`
for explicitly removing one terminal record. `EventQueue` and the generic
`BaseQueue` contract do not retain lifecycle entries and do not expose this
operation. Scheduled retention cleanup and an explicit prune share one internal
pruning operation, so they have identical effects: the final observer
notification is emitted from an immutable copy after durable removal.
Explicit pruning
rejects queued and running entries.

Lookup and pruning of an absent identified AsyncQueue entry SHALL raise
`QueueEntryNotFoundError`. This replaces the ambiguous use of
`QueueEmptyException` for missing retained records, while leaving
`QueueEntryMissingError` specific to a reliable-delivery claim that disappears.

The final event is an immutable entry-shaped copy transitioned to `terminated`.
`terminated` is the lifecycle state after every completed state, but this final
copy is never stored in a backend. This lets observers remove an entry from
their projections while retaining its identifier and last known fields for
event ordering. Cleanup publishes the termination event from the immutable copy
without retaining a `terminated` record.

### Pre-dispatch failure is terminal

`succeeded` remains a handler outcome and therefore requires a prior `running`
state. `failed` also remains valid from `running`, but it is additionally valid
directly from `queued` when queue processing determines that handler dispatch
cannot occur, such as after a transport or validation failure. A failed entry,
like every completed entry, transitions only to `terminated` when it is pruned.

## Risks / Trade-offs

- [Status queried after expiry] → lookup reports not found; observers receive
  the final `terminated` copy; document the retention duration
  and raise `QueueEntryNotFoundError` for the missing ID.
- [Pre-dispatch failure has no handler runtime] → retain `dispatched_at` as
  absent while setting `finished_at` for a direct `queued` to `failed`
  transition.
- [Explicit pruning races scheduled cleanup] → both paths use the same pruning
  operation; the loser receives `QueueEntryNotFoundError`.
