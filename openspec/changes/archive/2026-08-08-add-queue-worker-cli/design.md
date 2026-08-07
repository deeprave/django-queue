## Context

The generic worker exists but Django applications otherwise need custom
boilerplate to run it outside their web process.

## Goals / Non-Goals

**Goals:** Create and run one worker for each queue with an explicitly
configured handler, and handle SIGINT/SIGTERM.

**Non-Goals:** Application-module handler discovery, process supervision, or
reliable-delivery semantics.

## Decisions

`runqueues` initialises the standard `QUEUES` registry, then reads an optional
`HANDLER` dotted import path from each queue definition. For every queue with a
handler, it imports that handler and creates an `AsyncQueueWorker` for that
queue/handler pair. The command owns `asyncio.run()`, runs those workers
concurrently, and cancels and awaits all of them during signal shutdown.

Queues without `HANDLER` remain available to application code but are not
dispatched by `runqueues`. `HANDLER` is command metadata: the configured queue
registry must preserve it for the command without passing it into the queue
backend constructor. Invalid handler paths or non-asynchronous handlers are
configuration errors reported before workers begin dispatching.

Having no configured handlers is a successful no-op: `runqueues` reports that
there is nothing to start and exits with status zero. When handlers are
configured, the command reports the total number it will start and identifies
each queue alias as its worker starts. This output is operational command
feedback, not the later worker-observability API.

An unexpected worker failure exits the command non-zero only when it leaves no
workers running. When other workers remain, `runqueues` logs the failed alias
and continues running them without cancellation or replacement. If those
workers later fail, the final remaining failure exits the command non-zero.
Resetting and restarting failed workers is supervision and is deferred.

## Risks / Trade-offs

- [Invalid factory path] → fail before starting with an actionable error.
