## MODIFIED Requirements

### Requirement: Provide intrinsic queue lifecycle observers
The system SHALL export `queue_observer(queue_name, callback, entry_id=None)`,
which registers one process-local callback for a named AsyncQueue and returns
a subscription object with an `unsubscribe()` method. The system SHALL also
support the same registration through decorator syntax,
`@queue_observer(queue_name, entry_id=None)`, applied to a sync or async
callable; the decorator SHALL return the original callable unchanged and
SHALL make a subscription available for it once registration is activated.
The optional entry ID filter narrows delivery to one entry. For a given
queue alias, the system SHALL maintain exactly one active observer runtime
backing all local registrations for that alias, regardless of how many
threads or registrations trigger it. The API SHALL NOT require a separate
`QUEUES` definition, Django Channels, or WebSockets.

#### Scenario: Register a queue observer
- **WHEN** an application registers a callback for a named async queue
- **THEN** the process starts or reuses its local notification runtime and
  retains the callback until it is unsubscribed

#### Scenario: Register an observer via decorator
- **WHEN** an application decorates a callable with `@queue_observer(queue_name)`
- **THEN** the callable is registered as a lifecycle observer for that queue
  and the decorator returns the original callable unchanged

#### Scenario: Unsubscribe dynamically
- **WHEN** application code invokes `subscription.unsubscribe()`
- **THEN** the runtime SHALL NOT invoke that callback for later snapshots

#### Scenario: Unsubscribe a decorator-registered observer
- **WHEN** application code unsubscribes an observer that was registered by
  decorator, whether before or after its registration has been activated
- **THEN** the runtime SHALL NOT invoke that callback for any snapshot,
  including ones pending at the moment of unsubscription

#### Scenario: Filter an observer to one entry
- **WHEN** an application registers an observer with an entry ID filter
- **THEN** the observer receives only snapshots whose ID matches that entry

#### Scenario: Reject an event queue observer
- **WHEN** an application calls `queue_observer` for an EventQueue
- **THEN** it raises an error identifying that lifecycle observation requires
  an AsyncQueue

#### Scenario: Serve concurrent registrations for one alias from a single runtime
- **WHEN** two different threads each register an observer for the same
  queue alias for the first time
- **THEN** both observers are served by the same single process-wide
  runtime, and no additional backend connection is created for the second
  registration

## ADDED Requirements

### Requirement: Defer decorator-registered observer activation until runtime start
The system SHALL record a decorator-registered observer without performing
any backend query or opening any backend connection at decoration time.
Activation of a decorator-registered observer — obtaining retained entry
snapshots and beginning to receive published snapshots — SHALL happen only
when the process-wide observer runtime starts.

#### Scenario: Import a module defining a decorated observer
- **WHEN** a Django process imports a module that decorates a function with
  `@queue_observer(queue_name)`, before the observer runtime has started
- **THEN** the import completes without querying any queue backend or
  opening any backend connection

#### Scenario: Activate a decorator-registered observer at runtime start
- **WHEN** the process-wide observer runtime starts
- **THEN** every previously decorated observer becomes active and begins
  receiving retained and future lifecycle snapshots for its named queue
