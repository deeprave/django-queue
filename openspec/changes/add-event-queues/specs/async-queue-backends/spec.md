## ADDED Requirements

### Requirement: Separate task and event queue semantics
The system SHALL provide `AsyncQueue` and `EventQueue` semantic base classes
beneath `BaseQueue`. Existing Redis and memory queues SHALL retain AsyncQueue
semantics. Redis and memory event queue variants SHALL remove consumed,
rejected, and expired events instead of persisting terminal states. Provider
composition and transport-specific delivery behaviour are defined by the
`provider-composition` capability.

#### Scenario: Retain an AsyncQueue outcome
- **WHEN** an AsyncQueue worker records a terminal outcome
- **THEN** its entry remains available under the existing lifecycle contract

#### Scenario: List retained AsyncQueue snapshots
- **WHEN** an application or observer needs its initial AsyncQueue state
- **THEN** it can call `alist()` (or synchronous `list()`) to obtain the
  retained entry snapshots

#### Scenario: Remove a consumed event
- **WHEN** an event worker acknowledges an event it owns
- **THEN** the backend removes its pending representation and entry record
