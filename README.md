# Django Queue

This is an implementation of message queues for Django.

## Requirements

`django-queue` requires Python 3.14 or later. Queue entry IDs use the
standard-library UUIDv7 implementation introduced in Python 3.14.

## Message Queues

What are message queues? In Django, message queues enable independent and decoupled communication between parts of an
application or with external systems. For instance, one app can generate messages for another app to consume, avoiding
direct dependencies. This module implements a simple mechanism where a sender publishes messages, and consumers read and
remove them from the queue.

This module supports various queue types: first-in-first-out (FIFO), last-in-first-out (LIFO or stacks), and priority
queues, where messages with higher priority are consumed before lower-priority ones, regardless of their addition order.

## Implementation

This module currently implements two types of queues. Both use the same interface and are, to some extent,
interchangeable:

- **memory queues**: non-persistent and available only while the application is running.
- **redis queues**: persistent queues backed by a Redis server.

## Configuration

Queues are configured in the Django settings module, and use a simple and familiar configuration format like **DATABASES** and **CACHES**.

Example

```python
QUEUES = {
    "default": {
        "BACKEND": "django_queue.backends.RedisQueueJson",
        "LOCATION": f"redis://localhost:6379/12",
        "maxsize": 64,
    },
}

```

The above configures the queue backend to be redis, storing FIFO data in JSON format.

To implement a stack (FILO), the `django_queue.backends.RedisStackJson` can be used instead, or a `"stack": True` option added to the options.

## Usage

Within an application, data is added to the queue by using the `add` method:

Example

```python
from django_queue import queue
...
   queue.add({"some": "object", "with": "values"})
...
```

Priority queues require slightly different handling in that a priority should be set to determine the order in which messages are consumed and when added should be done as a `(priority, value)` tuple:

```python
from django_queue import queue
...
   queue.add((10, {"some": "object", "with": "values"}))
...
```

Multiple values can be added in the one `add()` call if required.

### Identified queue entries

The entry-oriented API is appropriate when a producer needs to poll the
outcome of work processed later. Payloads and handler results must be
JSON-serialisable. The queue generates the UUIDv7 identifier and owns all
lifecycle timestamps.

```python
from django_queue import queue

entry_id = queue.enqueue({"request_id": 42})
entry = queue.get_entry(entry_id)

assert entry.status == "queued"
```

An entry transitions through `queued`, `running`, and one terminal status:
`succeeded`, `failed`, or `cancelled`. Failed entries expose only an exception
type and safe message; the worker logs the traceback for diagnosis.

### Asynchronous worker

An application or management command explicitly owns the worker task. It must
not be started from a request handler or Django app initialisation hook.

```python
import asyncio

from django_queue import AsyncQueueWorker, queues


async def process_request(entry):
    return {"processed": entry.payload["request_id"]}


worker = AsyncQueueWorker(
    {"default": queues["default"]},
    {"default": process_request},
)
asyncio.run(worker.run())
```

The worker dispatches one entry at a time and runs until cancelled. On
cancellation it stops accepting new entries, gives an active handler its
configured grace period, then cancels it if needed. Delivery is best effort:
an unexpected process failure after dequeue and before the terminal outcome is
stored can lose that entry. Claim/acknowledge and recovery are planned
follow-up work.

If a terminal outcome cannot be persisted, the worker logs the infrastructure
failure and continues. When it can still read a `running` entry, it makes one
best-effort attempt to record a safe `QueuePersistenceError` failure outcome.
If it cannot confirm either terminal outcome, the worker raises
`QueuePersistenceError` rather than accepting further entries.

With all queues, the `get()`, `peek()` and `pull()` methods return the object.
With priority queues the priority is only used with and relevant to `add()`.
Identified entries have no priority parameter, so their worker dispatch remains
FIFO until priority-aware entry enqueueing is introduced.

## Queue Interface

All queues conform to the following interface:

#### Properties

- stack: returns True if the queue is a stack (LIFO) otherwise it is FIFO or priority based
- capacity: returns the queue capacity, 0 for unlimited

#### Methods

- add(item1[, item2, item3 ...]): add one or more items to the queue. With priority queues, items can be passed as `(priority, item)` tuples, although if not a tuple the default priority of 0 is defined. Priorities are evaluated as higher values = high priority, lower values = low priority. Priority can be positive or negative with 0 considered "normal".
- get(): retrieve and remove the next item from the queue.
- poll(): same as get(), but blocks if no item is available.
- peek(): retrieve but not remove the next item in the queue.
- size(): returns the number of items currently in the queue. `len(queue)` also returns this value.
- is_empty(): returns true if there are no items currently in the queue.
- clear(): remove all items from the queue.
- close(): closes and destroys the queue.
- the queue itself can be used in the context of a boolean: True if there are items in the queue else False.

#### Exceptions

- InvalidQueueBackendError: this indicates an issue with the Django QUEUES configuration.
- QueueFullException: operation (addition) attempted on a queue that has reached capacity
- QueueEmptyException: operation (get, peek or timed out poll) accepted on an empty queue
- QueueEncodingException: error occurred in encoding the item
- QueueValueError: error occurred in decoding an item
