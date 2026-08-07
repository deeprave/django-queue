## ADDED Requirements

### Requirement: Construct configured entry subclasses
Each queue backend SHALL create and restore entries with its alias's resolved
`ENTRY_CLASS`. The class MUST extend `QueueEntry`; the resulting value MUST
retain all base entry fields, immutable lifecycle behaviour, and JSON durable
representation. Fields the subclass declares MUST be persisted and restored
alongside the base fields without the subclass overriding any conversion
method. A backend MUST NOT instantiate an entry during settings or queue
construction; it SHALL do so only for enqueue, restore, or lifecycle operations
that require an entry value.

#### Scenario: Enqueue with a custom entry class
- **WHEN** a queue defines a valid `ENTRY_CLASS` subclass and a caller enqueues
  a JSON-serialisable payload
- **THEN** the backend stores and returns that entry subclass with the standard
  queued lifecycle fields

#### Scenario: Persist a field the subclass declares
- **WHEN** a queue's `ENTRY_CLASS` declares a JSON-serialisable field beyond the
  base entry's and an entry is stored and read back
- **THEN** the restored entry carries that field's value, with no conversion
  method overridden on the subclass

#### Scenario: Construct an idle configured queue
- **WHEN** Django initialises a queue with a valid custom `ENTRY_CLASS` but no
  entry operation occurs
- **THEN** the queue is constructed without creating an entry instance

#### Scenario: Restore a custom entry after a lifecycle transition
- **WHEN** a backend retrieves or updates an entry written with its configured
  entry subclass
- **THEN** it restores the configured subclass and preserves its standard
  lifecycle transition semantics
