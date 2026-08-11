## MODIFIED Requirements

### Requirement: Run configured queue handlers
The CLI SHALL import each configured queue `HANDLER`, create one
`AsyncQueueWorker` for each queue/handler pair, run those workers until
cancelled, and return a non-zero exit status for configuration errors. Where it
waits for an alias to become active, it SHALL await the backend rather than
dispatching that wait to a thread.

#### Scenario: Ignore queues without a handler
- **WHEN** a configured queue does not define `HANDLER`
- **THEN** `runqueues` does not create a worker for that queue

#### Scenario: Wait for activation without a worker thread
- **WHEN** `runqueues` waits for a configured alias to have pending entries
- **THEN** it awaits the backend and uses no thread other than the one running
  its event loop
