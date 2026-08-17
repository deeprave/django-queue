# Redis Claim Leases

## Purpose

Define broker-neutral reliable delivery and Redis claim-lease recovery.

## Requirements

### Requirement: Keep Redis reliable delivery internal to Redis workers

Redis-aware workers SHALL use their queue-owned Redis provider for claim,
renewal, settlement, and expired-claim recovery. Queue-facing APIs and the
common provider protocol SHALL NOT expose those delivery operations or Redis
storage details. A backend that does not use Redis reliable delivery SHALL use
its own transport-native worker model or retain best-effort dequeue behaviour.

#### Scenario: Dispatch on an unsupported backend
- **WHEN** a worker serves a queue without reliable-delivery support
- **THEN** it continues to dequeue and dispatch entries using the existing
  best-effort path

### Requirement: Recover expired claims
The system SHALL return a Redis entry with an expired unacknowledged lease to
pending visibility for another worker to claim.

#### Scenario: Recover a crashed worker claim
- **WHEN** a claim lease expires without acknowledgement
- **THEN** a recovery operation makes its entry pending again

### Requirement: Provide at-least-once delivery
The Redis worker SHALL atomically settle successful or failed terminal handling
with its owned claim and MUST document that recovered entries can execute more
than once.

#### Scenario: Settle terminal work
- **WHEN** a handler records a terminal outcome
- **THEN** its claim is released and is not recovered

### Requirement: Settle an owned claim atomically
The system SHALL persist a terminal entry and release its claim as one
owner-checked operation.

#### Scenario: Reject a stale worker terminal outcome
- **WHEN** a worker no longer owns a claim
- **THEN** it cannot overwrite the entry's terminal outcome while settling it
