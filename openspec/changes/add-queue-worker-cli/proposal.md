## Why

Applications need a supported Django management command to run generic queues
without embedding worker lifecycle code in each deployment.

## What Changes

- Add `manage.py runqueues`, loading configured queue handlers until interrupted.
- Provide predictable exit codes and graceful signal shutdown.

## Capabilities

### New Capabilities
- `runqueues-command`: Start and stop configured generic queue workers.

### Modified Capabilities
None.

## Impact

Adds a console entry point and documentation; depends on the generic worker.
