## ADDED Requirements

### Requirement: Register process-local event listeners
The system SHALL export `queue_listener` from `django_queue`. The decorator
SHALL register the decorated sync or async callable for one named event queue
and accept an optional `filter` callable receiving a QueueEntry and returning
a bool. Registration SHALL be process-local and SHALL NOT require a task handler.

#### Scenario: Register an asynchronous listener
- **WHEN** an application decorates an async function with `@queue_listener("events")`
- **THEN** the function is registered for the events queue in that Django process

#### Scenario: Skip a filtered event
- **WHEN** a listener filter returns false for an event
- **THEN** dispatch skips that listener and continues to the next listener

#### Scenario: Reject an AsyncQueue listener
- **WHEN** an application decorates a listener for an AsyncQueue
- **THEN** registration raises an error identifying that event listeners require
  an EventQueue

### Requirement: Dispatch listeners with rotating fairness
Each event queue worker SHALL visit local listeners in rotating order and SHALL
advance its cursor after every visit, including a filter miss and a `None`
result. The next event SHALL begin after the listener that ended the prior
event's dispatch cycle.

#### Scenario: Pass to the next listener
- **WHEN** an eligible listener returns `None`
- **THEN** the worker invokes the next listener in rotated order

#### Scenario: Rotate after consumption
- **WHEN** a listener consumes an event
- **THEN** the following event starts with the next listener, wrapping at the end

### Requirement: Settle listener outcomes without task results
An eligible listener returning `True` SHALL remove its event. A listener
returning `False` SHALL log a rejection and remove its event. An exception
from a listener or filter SHALL be logged and SHALL release the event after a
fixed short delay. An all-pass cycle SHALL release after the same delay. These
outcomes SHALL NOT persist a terminal task result.

#### Scenario: Consume an event
- **WHEN** an eligible listener returns `True`
- **THEN** the event is removed without a terminal entry record

#### Scenario: Reject an event
- **WHEN** an eligible listener returns `False`
- **THEN** the worker logs rejection and removes the event

#### Scenario: Retry an exceptional listener
- **WHEN** a listener or its filter raises
- **THEN** the worker logs the exception and makes the event available only after the delay

### Requirement: Run local dispatchers on a shared event runtime
The system SHALL start one process-wide event runtime for configured event
queues. It SHALL own one background thread and asyncio loop, with one event
worker task per configured event queue. Async listeners SHALL run in that loop;
sync listeners SHALL use the framework bridge and SHALL NOT block that loop.

#### Scenario: Share one runtime loop
- **WHEN** two event queues are configured in one Django process
- **THEN** their workers run as separate tasks on one event-runtime loop

### Requirement: Document delivery scope and ordering
The system SHALL document that Redis workers compete through leases, memory
event queues are process-local, and ordering is indeterminate with multiple
listeners, processes, or retries. Strict ordering SHALL require one listener in
one process.

#### Scenario: Run in multiple Django processes
- **WHEN** multiple processes listen to one Redis event queue
- **THEN** an event has at most one active claim owner without global ordering
