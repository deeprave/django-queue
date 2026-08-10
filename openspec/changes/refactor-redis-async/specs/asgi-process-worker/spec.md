## MODIFIED Requirements

### Requirement: Preserve process-local queue boundaries
The ASGI integration SHALL document that each ASGI server process owns an
independent worker and in-memory queue instances. It MUST NOT claim that an
in-memory queue can be consumed by another process, container, or external
`runqueues` worker. The local enqueue observation SHALL await the backend when
testing for pending entries rather than dispatching that test to a thread.

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

#### Scenario: Poll for local activity without leaving the loop
- **WHEN** the wrapper checks whether an alias has pending entries
- **THEN** it awaits the backend, and the lifespan's event loop is not blocked
  and no worker thread is used
