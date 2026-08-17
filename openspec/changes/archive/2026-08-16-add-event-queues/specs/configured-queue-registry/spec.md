## MODIFIED Requirements

### Requirement: Configure queue type extensions

The configured queue registry SHALL accept optional `WORKER` and `ENTRY_CLASS`
metadata for each alias. Each value MUST be either a class object or a non-empty
dotted import path. `WORKER` MUST resolve to the queue kind's compatible task
or event worker subclass, and `ENTRY_CLASS` MUST resolve to a `QueueEntry`
subclass. Omitted values SHALL use the backend-selected default worker and
entry classes. Settings initialisation MUST NOT instantiate either class, and
SHALL leave both configured values unchanged; it SHALL preserve `WORKER` on the
queue for a worker consumer to resolve.

#### Scenario: Configure a dotted worker and entry class
- **WHEN** a queue alias defines valid dotted `WORKER` and `ENTRY_CLASS` values
- **THEN** the registry retains both configured values unchanged, exposes the
  resolved entry class on the queue, and creates neither a worker nor an entry
  instance

#### Scenario: Reject an incompatible extension class
- **WHEN** a queue alias defines a worker that is not compatible with that
  queue's kind or backend, or an entry class that is not the required subtype
- **THEN** configuration raises an alias-specific configuration error before a
  worker is constructed

## ADDED Requirements

### Requirement: Restrict queue alias characters

Configured queue aliases SHALL be non-empty strings and SHALL NOT contain `*`,
`?`, `[` or `]`.

#### Scenario: Reject an unsafe queue alias
- **WHEN** a `QUEUES` alias contains `*`, `?`, `[` or `]`
- **THEN** configuration raises an alias-specific configuration error

### Requirement: Start configured event queues automatically
During Django application setup, the registry SHALL register process-local
event-runtime startup for the first HTTP request. That request SHALL start or
reuse the process-wide event runtime for every configured event queue and add
one dispatcher task per event queue. Async queues SHALL retain existing startup
behaviour and SHALL NOT start a worker during application setup.

#### Scenario: Initialise an event queue in a request-serving process
- **WHEN** a Django process with a valid event queue alias handles its first HTTP request
- **THEN** the process-wide runtime has one dispatcher task for that queue

#### Scenario: Initialise async queues only
- **WHEN** a Django process with no event queues handles an HTTP request
- **THEN** it starts no event dispatcher
