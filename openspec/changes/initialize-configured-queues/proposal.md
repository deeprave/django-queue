## Why

Named queues must be a validated Django service registry before application code or `runqueues` can use them.

## What Changes

- Initialize and validate all `QUEUES` settings during Django app setup.
- Expose configured queues through the existing registry without starting workers.

## Capabilities

### New Capabilities
- `configured-queue-registry`: Initialize named `QUEUES` services at Django startup.

### Modified Capabilities
None.

## Impact

Updates Django app configuration and queue connection initialization.
