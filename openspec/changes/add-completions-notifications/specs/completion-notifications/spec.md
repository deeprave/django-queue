## ADDED Requirements

### Requirement: Provide intrinsic completion listeners
The system SHALL provide an API that registers one or more process-local
callbacks for a queue entry ID and returns an unsubscribe handle. Registering
the first callback SHALL automatically start one idempotent local notification
runtime. The API SHALL NOT require a separate `QUEUES` definition, Django
Channels, or WebSockets.

#### Scenario: Register a completion listener
- **WHEN** an application registers a callback for an identified entry
- **THEN** the process starts or reuses its local notification runtime and
  retains the callback until it is invoked or unsubscribed

#### Scenario: Unsubscribe a completion listener
- **WHEN** an application invokes the unsubscribe handle returned by listener
  registration
- **THEN** the runtime SHALL NOT invoke that callback for later notifications

### Requirement: Deliver terminal completion notifications best-effort
The system SHALL publish a compact completion notification only after a worker
has successfully recorded an entry in the `succeeded`, `failed`, or `cancelled`
state. A locally registered callback whose entry ID matches a received
notification SHALL receive the immutable notification containing the entry ID,
queue identity, terminal status, and completion timestamp. Callback failures
MUST be logged and MUST NOT prevent later callbacks or queue processing.

#### Scenario: Receive a successful completion notification
- **WHEN** a worker records an entry as `succeeded` and a Django process has a
  callback registered for that entry ID
- **THEN** the callback receives a notification identifying that successful
  terminal outcome

#### Scenario: Isolate a callback failure
- **WHEN** a completion callback raises an exception
- **THEN** the runtime logs the exception and continues to process later
  notifications and callbacks

#### Scenario: Receive a memory-queue completion notification
- **WHEN** a memory-backed queue records an entry as terminal and a callback is
  registered in that same process for the entry
- **THEN** the intrinsic in-memory broker delivers the terminal notification to
  that callback without requiring a configured application queue

### Requirement: Catch up a completion that predates registration
The system SHALL retain each published completion notification as a
short-lived, entry-ID-keyed backend-local record before publishing it:
Redis-backed queues use Redis records and memory-backed queues use expiring
process-local records. Registration SHALL perform one lookup for that record
and invoke a matching callback when it exists. Expiry or an unavailable
notification transport MUST NOT change the stored queue-entry outcome.

#### Scenario: Register immediately after completion
- **WHEN** an entry reaches a terminal state immediately before an application
  registers its completion callback
- **THEN** the registration lookup finds the retained completion record and
  invokes the callback with its terminal outcome

#### Scenario: Register after a memory completion
- **WHEN** a memory-backed entry reaches a terminal state immediately before an
  application in that same process registers its completion callback
- **THEN** the registration lookup finds the retained local completion record
  and invokes the callback with its terminal outcome

#### Scenario: Notification publishing is unavailable
- **WHEN** an entry's terminal outcome has been stored but Redis notification
  publishing fails
- **THEN** the system logs the notification failure and preserves the stored
  terminal outcome without failing the worker solely for notification delivery
