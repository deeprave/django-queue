## ADDED Requirements

### Requirement: Dispose configured queues from the loop that owns their resources
The registry SHALL provide asynchronous disposal of configured queues, since a
backend's connection resources belong to the event loop that acquired them. The
synchronous disposal hook SHALL remain synchronous and delegate through the
framework's bridge. ASGI lifespan and `runqueues` SHALL await disposal from
their own resource-owning loops; Django provides no process-shutdown signal
that can do this safely for an arbitrary loop.

#### Scenario: Close synchronous-wrapper resources
- **WHEN** a synchronous host calls the registry disposal hook
- **THEN** resources acquired through its bridge loop are disposed and the hook
  remains a synchronous callable

#### Scenario: Close queues from asynchronous code
- **WHEN** asynchronous code awaits registry disposal
- **THEN** each configured queue releases the connection resources belonging to
  the running loop without leaving it
