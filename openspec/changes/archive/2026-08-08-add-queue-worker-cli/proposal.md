## Why

Applications need a supported Django management command to run generic queues
without embedding worker lifecycle code in each deployment.

## What Changes

- Add `manage.py runqueues`, loading handlers declared on configured queues until
  interrupted.
- Provide predictable exit codes and graceful signal shutdown.

## Capabilities

### New Capabilities
- `runqueues-command`: Start and stop configured generic queue workers.

### Modified Capabilities
- `configured-queue-registry`: Preserve optional handler metadata in each queue
  definition without passing it to the backend.

## Impact

Adds a console entry point, optional queue handler configuration, and
documentation; depends on the generic worker.
