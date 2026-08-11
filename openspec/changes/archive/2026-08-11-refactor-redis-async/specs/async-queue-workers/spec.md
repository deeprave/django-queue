## MODIFIED Requirements

### Requirement: Keep queue I/O from blocking the event loop
The asynchronous worker SHALL await queue operations directly and SHALL await
handlers directly. It MUST NOT dispatch queue operations to a worker thread:
keeping the event loop free is the backend's responsibility, discharged by the
backend being asynchronous.

#### Scenario: Run an asynchronous queue backend
- **WHEN** a worker dequeues or updates an entry
- **THEN** it awaits the backend operation, and other tasks on the worker's
  event loop continue to run while that operation is outstanding

#### Scenario: Dispatch without a worker thread
- **WHEN** a worker completes a full dispatch, from dequeue through the terminal
  outcome
- **THEN** no part of that dispatch is executed on a thread other than the one
  running the event loop
