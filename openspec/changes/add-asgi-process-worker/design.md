## Context

`AsyncQueueWorker` is reusable but currently needs application-specific task
lifecycle code. An ASGI process already has a lifecycle owned by its server,
which can safely own one explicitly configured worker. `MemoryQueue` is local
to its Python process and cannot be consumed by a separate `runqueues` process.

## Goals / Non-Goals

**Goals:** Provide an opt-in wrapper for Django's ASGI application that starts
one generic worker per ASGI process and shuts it down using the worker's
existing cooperative cancellation behaviour. Support end-to-end integration
tests that exercise a real request path, queue, worker, and status lookup
without Redis or a separate worker process.

**Non-Goals:** Starting workers from `AppConfig.ready()`, handler discovery from
settings, worker restart/supervision, sharing memory queues across threads or
processes, or changing the external `runqueues` command.

## Decisions

### Provide a Django ASGI lifespan wrapper

The public ASGI helper wraps the Django application returned by
`get_asgi_application()` and handles the ASGI `lifespan` scope itself. HTTP and
WebSocket scopes are delegated unchanged to the wrapped Django application.
This keeps worker ownership with the ASGI server rather than Django app setup.

The wrapper is intentionally for Django's ASGI application, not a generic
lifespan-composition framework. Supporting arbitrary nested applications that
also consume lifespan events would require bidirectional event proxying and is
not needed for this initial capability.

### Make handlers explicit

The wrapper accepts a mapping of queue aliases to asynchronous handlers and
uses the configured `queues` registry by default. Applications may pass an
explicit queue mapping for isolated tests or custom composition. This avoids
introducing a handler-settings format before the external worker command has
defined one.

### Let the host application enable the wrapper from its environment

The wrapper is disabled unless the host application's `asgi.py` applies it.
Applications commonly make that decision with an environment-derived Django
setting, for example `DJANGO_QUEUE_ASGI_WORKER=1`. The queue package does not
read that environment variable itself: handler functions are application code,
so the application must remain responsible for constructing the wrapper and
choosing its deployment mode.

### Bind worker lifecycle to ASGI lifecycle messages

On `lifespan.startup`, the wrapper creates and schedules one
`AsyncQueueWorker`, then sends `lifespan.startup.complete`. On
`lifespan.shutdown`, it cancels and awaits that task before sending
`lifespan.shutdown.complete`; the worker's cancellation grace period remains
the authority for active handlers.

If wrapper construction or startup fails, it sends `lifespan.startup.failed`
with a safe diagnostic message. An unexpected worker failure after startup is
logged and leaves no replacement worker; supervision and health reporting are
deferred to the observability roadmap.

### State the process-local boundary plainly

Each ASGI server process owns its own wrapper and worker. A `MemoryQueue` is
therefore useful only for producers and the worker in that same process; it is
not a cross-worker, cross-container, or external-worker queue. Redis-backed
queues remain appropriate where processes must share work.

Integration tests can pass an explicit `MemoryQueue` mapping to the wrapper
and to the application component that produces work. Keeping that object
explicit avoids relying on Django's per-context connection cache and proves the
request-to-worker contract without external Redis infrastructure.

### Warn whenever an in-process worker starts

On successful lifespan startup, the wrapper logs a prominent warning that the
in-process ASGI worker is not supported for production use. The warning directs
operators to an external worker with a shared backend such as Redis. This is a
runtime warning rather than documentation alone, because an environment toggle
can otherwise enable the wrapper unintentionally in a production deployment.

## Risks / Trade-offs

- [Multiple ASGI workers create multiple memory queues] → document that memory
  queues are process-local and require a single-process deployment for this
  use case.
- [Worker failure after startup is not visible to the ASGI protocol] → log the
  failure; add worker health/restart behaviour only in the observability
  change.
- [ASGI server skips lifespan support] → document that the integration requires
  lifespan to be enabled, as it is in supported production ASGI servers.
- [In-process worker is enabled in production] → emit a startup warning and
  direct operators to an external worker with a shared queue backend.
