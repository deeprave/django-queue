## ADDED Requirements

### Requirement: Compose providers into semantic queue facades
`AsyncQueue` and `EventQueue` SHALL provide generic queue-facing semantics by
composing a provider. They SHALL NOT inherit, mirror, or expose a provider's
transport operations as queue methods. Concrete backend queues, including
`RedisAsyncQueue`, `RedisEventQueue`, `MemoryAsyncQueue`, and
`MemoryEventQueue`, SHALL only select and inject their provider and default
worker behaviour.

#### Scenario: Construct a Redis async queue
- **WHEN** an application constructs `RedisAsyncQueue`
- **THEN** it receives an `AsyncQueue` semantic facade composed with a Redis
  provider and a Redis-aware default async-queue worker

#### Scenario: Use a queue-facing API
- **WHEN** application code produces, reads, or administers queue entries
- **THEN** it uses queue semantic operations and does not receive a provider
  instance or transport coordination value

### Requirement: Keep delivery semantics transport-specific
The common `QueueProvider` protocol SHALL initially declare only asynchronous
resource closure. It SHALL NOT declare a clock, storage, pending-work, claim,
renew, acknowledge, release, settle, recovery, retention, or pruning operation.
An operation SHALL be promoted into the common protocol only after multiple
providers require the same transport-independent contract. A transport-aware
worker SHALL implement delivery using its provider's native model.

#### Scenario: Redis delivery
- **WHEN** a Redis default worker dispatches an entry
- **THEN** it uses Redis claim, lease renewal, acknowledgement, settlement,
  recovery, and retention operations owned by the Redis provider

#### Scenario: Introduce a non-Redis transport
- **WHEN** a JetStream, NATS, Kafka, or SQS provider is added
- **THEN** it can select a worker that uses its native acknowledgement,
  visibility, or commit model without implementing Redis claim semantics

#### Scenario: Require a shared provider operation
- **WHEN** more than one provider needs the same transport-independent
  operation
- **THEN** that operation may be promoted into `QueueProvider` with scenarios
  covering each provider

### Requirement: Keep provider instances and ownership context internal
Only a transport worker and its queue-owned provider instance SHALL hold
delivery ownership context. Queue-facing producer, handler, listener, and
administration operations SHALL NOT accept or expose worker IDs, claim owners,
lease tokens, or equivalent transport coordination values. A queue SHALL NOT
publish its composed provider instance to application code. Provider methods
need not be artificially private: workers may call them through the queue-owned
provider they receive during backend-controlled construction.

#### Scenario: Dispatch an async-queue handler
- **WHEN** an async-queue handler returns a result or raises an exception
- **THEN** the transport worker records or retries the outcome without the
  handler receiving an ownership value

#### Scenario: Dispatch an event listener
- **WHEN** an event listener returns a value or raises an exception
- **THEN** the transport worker applies the backend-specific consume, reject,
  or retry outcome without the listener receiving an ownership value

### Requirement: Select workers by concrete backend
Each concrete queue backend SHALL declare an overridable default worker class
appropriate to its provider. `RedisAsyncQueue` and `RedisEventQueue` SHALL
select Redis-aware workers; memory queue variants SHALL select memory-aware
workers. Common worker base classes SHALL NOT access a composed provider or
require claim, acknowledgement, retry, renewal, recovery, or settlement
operations. Queue configuration SHALL continue to permit an explicit compatible
worker override.

#### Scenario: Run configured Redis queues
- **WHEN** Django starts configured Redis task and event queues
- **THEN** their respective Redis-aware workers are selected without callers
  supplying transport details

#### Scenario: Run configured memory queues
- **WHEN** Django starts configured memory async or event queues
- **THEN** their respective memory-aware workers are selected without callers
  supplying transport details

#### Scenario: Override a worker
- **WHEN** configuration supplies a compatible worker class for a concrete
  queue backend
- **THEN** the queue uses that override while preserving the backend's provider
  composition
