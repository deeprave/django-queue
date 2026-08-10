## ADDED Requirements

### Requirement: Dispose configured queues from the loop that owns their resources
The registry SHALL provide asynchronous disposal of configured queues, since a
backend's connection resources belong to the event loop that acquired them. The
synchronous disposal connected to Django's shutdown signal SHALL remain
synchronous and delegate through the framework's bridge, so host applications
and signal wiring are unchanged.

#### Scenario: Close queues at Django shutdown
- **WHEN** Django emits the shutdown signal that closes queues
- **THEN** every configured queue is disposed and the receiver remains a
  synchronous callable

#### Scenario: Close queues from asynchronous code
- **WHEN** asynchronous code awaits registry disposal
- **THEN** each configured queue releases the connection resources belonging to
  the running loop without leaving it
