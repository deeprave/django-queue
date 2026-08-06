## Context

The generic worker exists but Django applications otherwise need custom
boilerplate to run it outside their web process.

## Goals / Non-Goals

**Goals:** Run a factory-created worker and handle SIGINT/SIGTERM.

**Non-Goals:** Django integration, process supervision, or handler discovery.

## Decisions

`runqueues` reads configured queues and handler paths from Django settings,
creates an `AsyncQueueWorker`, and owns `asyncio.run()` and signal shutdown.

## Risks / Trade-offs

- [Invalid factory path] → fail before starting with an actionable error.
