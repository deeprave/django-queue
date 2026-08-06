## ADDED Requirements

### Requirement: Run an opt-in worker in Django ASGI lifespan
The system SHALL provide an opt-in ASGI wrapper for Django's ASGI application.
The wrapper MUST create one `AsyncQueueWorker` from explicitly supplied queue
handlers when it receives `lifespan.startup`, and it MUST not start a worker
during Django app configuration.

#### Scenario: Start a process worker
- **WHEN** an ASGI server sends `lifespan.startup` to an application wrapped
  with configured queue handlers
- **THEN** the wrapper starts one worker for that ASGI process and sends
  `lifespan.startup.complete`

### Requirement: Stop the process worker cooperatively
The ASGI wrapper SHALL cancel and await its worker when it receives
`lifespan.shutdown`. It MUST send `lifespan.shutdown.complete` only after the
worker has completed its existing cooperative cancellation behaviour.

#### Scenario: Shut down an active process worker
- **WHEN** an ASGI server sends `lifespan.shutdown` while the worker handles an
  entry
- **THEN** the wrapper stops the worker from accepting new entries, applies the
  configured handler grace period, and then sends `lifespan.shutdown.complete`

### Requirement: Preserve process-local queue boundaries
The ASGI integration SHALL document that each ASGI server process owns an
independent worker and in-memory queue instances. It MUST NOT claim that an
in-memory queue can be consumed by another process, container, or external
`runqueues` worker.

#### Scenario: Deploy multiple ASGI workers
- **WHEN** an application deploys multiple ASGI server processes with the
  wrapper enabled
- **THEN** each process operates its own in-memory queue and worker, while
  shared queue backends remain available to all processes

#### Scenario: Exercise a process-local worker in an integration test
- **WHEN** an integration test provides one explicit in-memory queue to the
  ASGI wrapper and its application component
- **THEN** it can exercise request-to-worker handling without a Redis service
  or a separate worker process

### Requirement: Leave process-worker enablement to the host application
The ASGI process-worker integration SHALL be disabled unless the host
application applies its wrapper. The package MUST NOT start a worker merely
because an environment variable is present; applications MAY use an
environment-derived setting to decide whether to apply the wrapper.

#### Scenario: Disable an in-process worker by deployment configuration
- **WHEN** an application's environment-derived setting elects not to wrap its
  Django ASGI application
- **THEN** no ASGI process worker is created

### Requirement: Warn against production in-process workers
The ASGI wrapper MUST log a warning whenever it starts a process worker. The
warning MUST state that the in-process ASGI worker is not supported for
production use and direct operators to an external worker with a shared queue
backend.

#### Scenario: Start an in-process worker
- **WHEN** an ASGI server starts the configured process worker successfully
- **THEN** the wrapper logs a warning that the worker is for local,
  single-process use and is not supported for production

### Requirement: Surface startup failures safely
The ASGI wrapper MUST report failure to construct or start its worker through
`lifespan.startup.failed` with a safe message. It SHALL log unexpected worker
failure after startup and MUST NOT silently start a replacement worker.

#### Scenario: Reject an invalid handler registration
- **WHEN** lifespan startup cannot construct the worker from the supplied
  handlers and queues
- **THEN** the wrapper sends `lifespan.startup.failed` and does not run a
  worker
