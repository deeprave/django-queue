## Context

Claim/ack creates ownership but cannot recover an abandoned claim.

## Goals / Non-Goals

**Goals:** Lease claims, reclaim expired work, and acknowledge terminal work.

**Non-Goals:** Exactly-once execution or arbitrary retry policies.

## Decisions

Claims include a Redis-clock lease deadline. Workers renew while handling; a
recovery operation atomically returns expired claims to pending. Terminal
handling acknowledges claims. Duplicate execution is explicitly possible.

Lease renewal extends the queue heartbeat with ownership validation: a worker
may renew only the claim it still owns. A lease duration must exceed the entry
budget by at least the worker cancellation grace period, so an orderly
cancellation cannot make a still-running handler reclaimable prematurely.

## Risks / Trade-offs

- [Lease expires during a slow task] → renewal and documented at-least-once duplicates.
