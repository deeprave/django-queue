## MODIFIED Requirements

### Requirement: Enqueue identified JSON-serialisable entries
The system SHALL provide an entry-oriented enqueue operation that accepts any
JSON-serialisable payload value, generates a UUID version 7 identifier, records
the queue namespace and `queued_at` timestamp, persists a `queued` entry, and
returns the generated identifier. The operation MUST reject a payload that does
not survive JSON serialisation before it persists a pending entry. The operation
SHALL be awaitable, and SHALL remain callable synchronously under its existing
name for callers that are not running on an event loop.

#### Scenario: Enqueue a JSON value
- **WHEN** a caller enqueues a JSON-serialisable payload on a named queue
- **THEN** the system returns a UUIDv7 identifier and persists an entry with
  `status` equal to `queued` and a non-null `queued_at` timestamp

#### Scenario: Reject a non-JSON payload
- **WHEN** a caller enqueues a value that cannot be JSON serialised
- **THEN** the system raises a serialisation error and does not create a pending
  queue entry

#### Scenario: Enqueue from asynchronous code
- **WHEN** asynchronous code awaits the enqueue operation
- **THEN** it receives the same identifier and persists the same entry as the
  synchronous call, without leaving the event loop
