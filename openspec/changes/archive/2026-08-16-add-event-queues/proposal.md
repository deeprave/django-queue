## Why

The current queues are durable async queues: a worker executes one handler and
persists a terminal result. Applications also need lightweight event delivery,
where locally registered consumers compete to handle a transient event and an
unhandled event expires rather than growing retained entry state indefinitely.

## What Changes

- Add explicit AsyncQueue and EventQueue semantic base classes beneath
  `BaseQueue`, while retaining compatible names for existing task backends.
- Add a public `QueueProvider` protocol, with memory and Redis providers that
  encapsulate backend-specific storage, lease, and connection behaviour.
- Add Redis and in-memory event queue backends, a public `queue_listener`
  decorator, and process-local listener registration.
- Start one process-wide event runtime automatically for configured event
  queues; it shares one asyncio loop across queue dispatchers.
- Define listener dispatch, rotating fairness, acknowledgement, delayed retry,
  expiry, and intentionally indeterminate multi-process ordering semantics.
- Interpret `timeout_seconds` as an unconsumed-event lifetime for event queues;
  event queues default it to 60 seconds.
- Follow up the completed event-queue implementation by separating generic
  queue semantics from transport-specific delivery semantics. Queue facades
  will compose providers, while Redis-aware workers own Redis claims, leases,
  acknowledgement, recovery, retention, and Pub/Sub behaviour. This keeps the
  queue model open to transports such as JetStream, NATS, Kafka, and SQS whose
  native delivery contracts do not resemble Redis claims.

## Capabilities

### New Capabilities

- `event-queue-listeners`: Register and dispatch process-local event listeners
  for configured Redis and memory event queues.

### Modified Capabilities

- `async-queue-backends`: Define task and event queue semantic contracts and
  their acknowledgement and removal operations.
- `configured-queue-registry`: Initialise and run configured event queues
  automatically during Django application setup.
- `queue-entries`: Define event-entry removal and the event-specific meaning of
  `timeout_seconds`.
- `timeout-governance`: Resolve event expiration independently from task-handler
  execution budgets.
- `provider-composition`: Make providers composed transport implementations and
  make backend-selected workers responsible for delivery semantics.

## Impact

Adds provider, event backend, and worker/runtime modules, listener registration
exported from `django_queue`, Redis lease/requeue and memory expiry support,
settings validation, tests, and documentation. Existing async-queue delivery and
stored terminal-entry semantics remain unchanged.

The follow-up provider-composition work changes internal architecture and the
unreleased provider surface. It removes the misleading universal claim/settle
contract before other transport providers are introduced, while preserving the
public queue, producer, handler, and listener experience.
