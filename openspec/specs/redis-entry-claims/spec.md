# Redis Entry Claims

## Purpose

Define atomic Redis claim ownership for identified queue entries.

## Requirements

### Requirement: Claim entries atomically
Redis queues SHALL atomically remove a pending entry from pending visibility and
record its worker-owned claim before returning it.

#### Scenario: Competing workers claim one entry
- **WHEN** two workers attempt to claim the same pending entry
- **THEN** exactly one worker receives that entry

### Requirement: Acknowledge owned claims
The system SHALL acknowledge a claim only when the requesting worker ID matches
the claim owner.

#### Scenario: Reject another worker acknowledgement
- **WHEN** a different worker acknowledges a claim
- **THEN** the claim remains recorded
