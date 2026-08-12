# Redis claim-lease model coverage

## Scope

`redis_claim_leases.tla` is a finite, broker-neutral state model of reliable
delivery. Its TLC configuration has two entries and two workers. A successful
run establishes that the listed properties hold for that finite model; it does
not prove the Redis implementation or the application.

## Resolved OpenSpec requirements

| Model element | Resolved requirement |
| --- | --- |
| `MarkRunning`, `Settle` | `async-queue-workers` — Dispatch registered queue handlers asynchronously |
| `PendingIsUnclaimedQueued`, `RunningHasOwner`, `TerminalHasNoClaim` | `queue-entries` — Record entry lifecycle outcomes |
| `Crash`, `Expire`, `RecoverExpired` | `add-redis-lease-recovery` delta: `redis-claim-leases` — Recover expired claims |
| `Claim`, `Renew`, `MarkRunning`, `Settle`, `RecoverExpired` | `add-redis-lease-recovery` delta: `redis-claim-leases` — Expose broker-neutral reliable-delivery capability |
| `Settle`, `TerminalHasNoClaim`, `SettlementIsOwnedTerminal` | `add-redis-lease-recovery` delta: `redis-claim-leases` — Settle an owned claim atomically |
| `RecoverExpired`, `RecoveredEntryIsUnsettled` | `add-redis-lease-recovery` delta: `queue-entries` — Recover an abandoned running entry |
| `Claim`, `Renew`, `Crash`, `Expire`, `RecoverExpired`, stale-action guards | `add-redis-lease-recovery` delta: `redis-claim-leases` — Provide at-least-once delivery; `async-queue-workers` — Hand off a lost claim |

## Decisions encoded

- Recovery applies to an expired claim on either a `queued` or `running`
  entry. It always returns the existing entry ID to pending work. A `running`
  entry becomes `queued`; a claimed-but-not-running entry remains `queued`.
- Settlement and claim release are one atomic transition. A worker cannot renew,
  mark running, or settle unless it is still the owner.
- `Expire` is an abstract representation of a lease reaching its deadline.
  `Renew` and `RecoverExpired` race atomically: either can occur first.
- `Crash` prevents that worker from taking further protocol actions; expiry and
  recovery represent the later hand-off.

## Assumptions and exclusions

- The model omits Redis commands, scripts, client errors, JSON, payloads,
  priorities, queue capacity, TTL cleanup, clock calibration, Django setup,
  and handler business logic.
- It covers claim-lease backends only. Non-claim-lease best-effort delivery is
  intentionally outside this model.
- It has no liveness property yet. Eventual recovery requires an explicit
  fairness assumption and will be added only after safety properties settle.
- `cancelled` and `timeout` are modelled as terminal lifecycle values, not as
  handler-control algorithms.
