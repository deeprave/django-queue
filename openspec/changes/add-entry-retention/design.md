## Context

Entry lookup is useful for status APIs but must not grow forever.

## Goals / Non-Goals

**Goals:** Bound terminal-record lifetime consistently across memory and Redis.

**Non-Goals:** Archival storage or retention of pending/running entries.

## Decisions

Use an optional terminal-entry TTL. Redis applies key expiry when terminal state
is recorded; memory queues prune expired terminal records during public access
and expose explicit cleanup. The default remains no expiry for compatibility.

## Risks / Trade-offs

- [Status queried after expiry] → lookup reports not found; document the TTL.
