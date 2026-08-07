## Purpose

Define Django's validated registry of named queue services.

## Requirements

### Requirement: Initialise configured queues

The Django app SHALL validate and initialise every `QUEUES` alias idempotently
during application setup without starting a worker. An optional `HANDLER`
dotted import path is command metadata for `runqueues` and MUST NOT be passed
to the queue backend constructor.

#### Scenario: Access a configured service

- **WHEN** Django initialisation completes with valid `QUEUES`
- **THEN** application code can retrieve each configured queue by alias

#### Scenario: Configure a queue handler

- **WHEN** a valid queue definition includes `HANDLER`
- **THEN** application setup initialises the queue normally and `runqueues` can
  use the handler path to create that queue's worker

### Requirement: Configure queue type extensions

The configured queue registry SHALL accept optional `WORKER` and `ENTRY_CLASS`
metadata for each alias. Each value MUST be either a class object or a non-empty
dotted import path. `WORKER` MUST resolve to an `AsyncQueueWorker` subclass and
`ENTRY_CLASS` MUST resolve to a `QueueEntry` subclass. Omitted values SHALL use
the generic default classes. Settings initialisation MUST NOT instantiate either
class, and SHALL leave both configured values unchanged; it SHALL preserve
`WORKER` on the queue for a worker consumer to resolve.

#### Scenario: Configure a dotted worker and entry class

- **WHEN** a queue alias defines valid dotted `WORKER` and `ENTRY_CLASS` values
- **THEN** the registry retains both configured values unchanged, exposes the
  resolved entry class on the queue, and creates neither a worker nor an entry
  instance

#### Scenario: Reject an incompatible extension class

- **WHEN** a queue alias defines an extension that cannot be imported or is not
  the required base-class subtype
- **THEN** configuration raises an alias-specific configuration error before a
  worker is constructed

### Requirement: Isolate type metadata from backend options

The registry SHALL preserve `WORKER` and `ENTRY_CLASS` as queue metadata and
MUST NOT pass either public metadata value to the backend constructor. It SHALL
make the resolved entry class available through the common entry-factory
boundary after backend construction.

#### Scenario: Construct a backend with type metadata

- **WHEN** a configured queue defines `WORKER` or `ENTRY_CLASS`
- **THEN** its backend receives only backend-supported options
