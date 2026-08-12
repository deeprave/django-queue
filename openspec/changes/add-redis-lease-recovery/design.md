## Context

Claim/ack creates ownership but cannot recover an abandoned claim.

## Goals / Non-Goals

**Goals:** Lease claims, reclaim expired work, and settle terminal work.

**Non-Goals:** Exactly-once execution or arbitrary retry policies.

## Decisions

`BaseQueue` defines the broker-neutral claim-and-lease operations used by
workers: claim, renew, settle, and recover. Backends that cannot provide
reliable delivery explicitly report the capability as unsupported, allowing the
worker to retain its existing best-effort dequeue path. Redis is the first
implementation; its keys, scripts, `TIME`, and claim-record layout remain
private so future brokers can map the operations to their native mechanisms.

Claims include a Redis-clock lease deadline. Workers renew while handling; a
recovery operation atomically returns expired claims to pending. Terminal
handling settles claims. Duplicate execution is explicitly possible.
Recovery resets an abandoned entry to `queued` before it returns the entry ID to
pending work, so the retry receives its own `dispatched_at` timestamp and
follows the normal lifecycle path. Each recovery operation processes a bounded
batch; workers invoke it on a short cadence rather than on every idle poll.

Lease renewal extends the queue heartbeat with ownership validation: a worker
may renew only the claim it still owns. A lease duration must exceed the entry
budget by at least the worker cancellation grace period, so an orderly
cancellation cannot make a still-running handler reclaimable prematurely.
Workers renew automatically at half of a lease up to 60 seconds, two thirds of
a lease up to 10 minutes, and three quarters for longer leases. The increasing
interval avoids unnecessary renewal traffic while retaining a meaningful
recovery window.

Workers claim with the finite backend default, then immediately renew to the
resolved execution budget plus cancellation grace before recording `running` or
invoking the handler. This keeps budget precedence in the worker rather than
requiring storage backends to understand worker configuration.

Terminal handling uses `settle_claim(worker_id, terminal_entry)`. The operation
verifies that the worker still owns the claim, persists the immutable terminal
entry, and removes the claim atomically. A stale worker therefore cannot
overwrite a recovered entry's terminal record. A lost claim is a normal
at-least-once hand-off: its former worker stops handling that entry and
continues with later work, while recovery or the new owner records the retry's
outcome. This is distinct from an infrastructure persistence failure, where no
owner outcome can be confirmed and the worker must stop after its safe fallback
also fails.

## Risks / Trade-offs

- [Lease expires during a slow task] → renewal and documented at-least-once duplicates.
