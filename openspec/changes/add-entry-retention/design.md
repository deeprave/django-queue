## Context

Entry lookup is useful for status APIs but must not grow forever.

## Goals / Non-Goals

**Goals:** Bound terminal-record lifetime consistently across memory and Redis.

**Non-Goals:** Archival storage or retention of pending/running entries.

## Decisions

Use an optional terminal-entry retention duration. Backends perform explicit
terminal-entry cleanup so an AsyncQueue can publish a final observer event
before deleting its durable record. The default remains no expiry for
compatibility.

The final event is an immutable observer-only copy of the entry with state
`terminated`. It is not a `QueueEntry`, is never stored in a backend, and does
not extend the durable queue-entry lifecycle. This lets observers remove an
entry from their projections while retaining its identifier and last known
fields for event ordering. Cleanup publishes the termination event before it
deletes the backing entry record.

## Risks / Trade-offs

- [Status queried after expiry] → lookup reports not found; observers receive
  the final `terminated` copy before removal; document the retention duration.
