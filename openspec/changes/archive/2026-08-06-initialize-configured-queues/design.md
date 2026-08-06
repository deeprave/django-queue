## Context

`QUEUES` is currently resolved lazily, while the worker command needs a validated registry.

## Goals / Non-Goals

**Goals:** Validate settings and idempotently initialise queue services.

**Non-Goals:** Starting workers or consuming queue data in `ready()`.

## Decisions

`DjangoQueueConfig.ready()` invokes an idempotent registry initialiser. It validates every alias and constructs cached service objects. Worker execution is reserved for `runqueues`.

## Risks / Trade-offs

- [Startup configuration failure] → report queue alias and invalid setting clearly.
