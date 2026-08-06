## Why

An external `runqueues` process cannot consume an in-memory queue, and Django
app startup must not create background work implicitly. ASGI applications need
an explicit, process-local way to run the generic worker for queues that are
intentionally local to one application process, particularly for integration
tests that exercise request-to-worker behaviour without external infrastructure.

## What Changes

- Add an opt-in ASGI lifespan wrapper that starts an `AsyncQueueWorker` after
  ASGI startup and shuts it down cooperatively during ASGI shutdown.
- Require the application to provide the worker's queue handlers explicitly.
- Document process-local delivery boundaries, including that each ASGI server
  worker has an independent in-memory queue and worker.
- Support the wrapper as an integration-test harness while warning against its
  use in production.

## Capabilities

### New Capabilities

- `asgi-process-worker`: Run a configured generic queue worker within one ASGI
  application process through the ASGI lifespan protocol.

### Modified Capabilities

None.

## Impact

Adds an ASGI integration module, lifespan tests, and configuration guidance.
It reuses the generic `AsyncQueueWorker`; it does not change external worker
execution, queue storage, or best-effort delivery semantics.
