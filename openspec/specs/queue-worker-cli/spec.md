# Queue Worker CLI

## Purpose

Define the `runqueues` management command that starts and stops workers for the
handlers declared on configured queues.

## Requirements

### Requirement: Run configured queue handlers
The CLI SHALL import each configured queue `HANDLER`, create one
`AsyncQueueWorker` for each queue/handler pair, run those workers until
cancelled, and return a non-zero exit status for configuration errors.

#### Scenario: Ignore queues without a handler
- **WHEN** a configured queue does not define `HANDLER`
- **THEN** `runqueues` does not create a worker for that queue

### Requirement: Report worker startup
The CLI SHALL report the total configured handler count and each queue alias as
its worker starts. It SHALL exit successfully, with an explicit no-handler
message, when no configured queue defines `HANDLER`.

#### Scenario: Start configured handlers
- **WHEN** configured queues define one or more valid handlers
- **THEN** `runqueues` reports the handler count and each queue alias as its
  worker starts

#### Scenario: No configured handlers
- **WHEN** no configured queue defines `HANDLER`
- **THEN** `runqueues` reports that there are no handlers and exits with status
  zero

#### Scenario: Graceful termination
- **WHEN** the worker process receives SIGTERM
- **THEN** it cancels and awaits the worker before exiting successfully
