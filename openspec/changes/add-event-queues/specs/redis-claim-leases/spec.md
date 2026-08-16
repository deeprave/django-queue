## MODIFIED Requirements

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
