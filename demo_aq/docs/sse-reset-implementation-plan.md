# AsyncQueue SSE and Clean-Start Implementation Plan

> **For agentic workers:** Execute this plan inline in the current workspace.
> Do not create a worktree or subagents.

**Goal:** Replace dashboard polling with observer-triggered SSE updates and
reset all `demo` queue state before each publisher-worker run.

**Architecture:** `DashboardProjection` gains a condition-protected version
counter. Its SSE generator emits an initial full projection and then emits only
when an observer callback updates that version. The management command clears
the `demo` Redis entry namespace and raw queue key in the same `asyncio.run()`
before enqueuing its random batch.

**Tech Stack:** Django `StreamingHttpResponse`, browser `EventSource`,
`django_queue.queue_observer`, Redis asyncio client.

## Global Constraints

- Do not use WebSockets, polling, static assets, a database, or direct Redis
  reads for dashboard display.
- Keep terminal rows while Redis retains their records.
- `manage.py demo` clears only the configured `demo` queue state and assumes
  no other demo worker is active during reset.
- Redis Pub/Sub messages are transient and are not reset.
- An already-open dashboard discards its old local projection only when the
  user activates Refresh after a new demo run begins.
- Render timestamps as non-wrapping `YYYY-MM-DDTHH:mm:ssZ` values.
- This demo deliberately skips automated tests; verify the running services
  directly instead.

---

### Task 1: Add observer-triggered SSE projection events

**Files:**
- Modify: `demo_aq/dashboard/projection.py`
- Modify: `demo_aq/dashboard/views.py`
- Modify: `demo_aq/dashboard/urls.py`

**Consumes:** Existing `DashboardProjection.update(QueueEntry)`, the
lock-protected row dictionary, and Django `StreamingHttpResponse`.

**Produces:** `DashboardProjection.events()` yielding SSE frames and `GET
/events/` returning `text/event-stream`.

- [x] **Step 1: Version projection changes under a condition**

  Add `self._changed = threading.Condition(self._lock)` and
  `self._version = 0`. In `update()`, replace the row, increment `_version`,
  and call `self._changed.notify_all()` while the condition lock is held.

- [x] **Step 2: Implement the SSE generator**

  Add `events()` as a synchronous generator. Start with `version = -1` so the
  first iteration emits the complete sorted projection immediately. Thereafter,
  wait up to 15 seconds for `_version` to differ; emit
  `data: {"entries": [...]}` followed by two newlines on change, otherwise
  emit `: keepalive` followed by two newlines. JSON-encode the complete row
  list so a reconnecting browser can reconstruct the whole table.

- [x] **Step 3: Replace the JSON state view with an SSE view**

  Replace `state()` with `events()`, returning:

  ```python
  response = StreamingHttpResponse(projection.events(), content_type="text/event-stream")
  response["Cache-Control"] = "no-cache"
  response["X-Accel-Buffering"] = "no"
  return response
  ```

  Keep `index()` and `refresh()` unchanged. Replace the `state/` route with an
  `events/` route named `events`.

- [x] **Step 4: Directly verify one SSE request**

  Start the local dashboard, then run
  `curl --no-buffer http://127.0.0.1:8000/events/`. Confirm the first frame
  contains retained rows, and a second frame appears only after the worker
  creates or changes an entry.

### Task 2: Use EventSource and compact timestamps in the template

**Files:**
- Modify: `demo_aq/dashboard/templates/dashboard/index.html`

**Consumes:** `GET /events/` SSE frames with `{"entries": [...]}` payloads.

**Produces:** One browser event stream that upserts table rows without repeated
HTTP requests.

- [x] **Step 1: Replace interval polling with EventSource**

  Remove `updateEntries()` and `setInterval`. Create:

  ```javascript
  const stream = new EventSource("{% url 'events' %}");
  stream.onmessage = ({ data }) => {
    JSON.parse(data).entries.forEach(render);
  };
  stream.onerror = (error) => console.error(error);
  ```

  Keep the existing `render()` row creation/update path and use `textContent`
  for all cells.

- [x] **Step 2: Format timestamps without spaces or wrapping**

  Render non-null timestamps as:

  ```javascript
  new Date(value * 1000).toISOString().slice(0, 19) + "Z"
  ```

  Keep null values as `—`. Add a `.timestamp { white-space: nowrap; }` rule
  and apply that class to the queued and finished cells when building a row.

- [x] **Step 3: Directly verify browser updates**

  Open the dashboard, inspect that only one `/events/` request remains open,
  then run `python manage.py demo --min 2 --max 2`. Confirm two new rows appear
  and their state/finished cells update without repeated `/state/` requests.

### Task 3: Reset `demo` queue state before publishing

**Files:**
- Modify: `demo_aq/dashboard/management/commands/demo.py`
- Modify: `demo_aq/README.md`
- Modify: `openspec/changes/add-asyncqueue-demo/tasks.md`

**Consumes:** The configured Redis-backed `queues["demo"]`, its public
`queue_name`, and its loop-local Redis client used by `aenqueue()`.

**Produces:** `_clear_demo_queue(queue)` completing before any batch enqueue.

- [x] **Step 1: Delete only the demo queue's durable keys**

  Implement `_clear_demo_queue(queue)` inside the command module. Use
  `client = queue._async_redis()` and asynchronously scan
  `f"{queue.queue_name}:entries:*"`; delete collected keys in one `DEL` call.
  Also delete `queue.queue_name` to clear the raw-item queue key. Do not issue
  `FLUSHDB`, `FLUSHALL`, or any Pub/Sub operation.

- [x] **Step 2: Reset before publishing in the existing async run**

  At the start of `_run(messages)`, after resolving `queues["demo"]`, await
  `_clear_demo_queue(queue)`. Only then build payloads, await all `aenqueue()`
  calls, and construct the `AsyncQueueWorker`. Keep the single `asyncio.run()`
  in `handle()`.

- [x] **Step 3: Document the reset behavior**

  State in `README.md` that every `demo` command run replaces the existing
  `demo` entries, and it assumes only one publisher-worker is active.

- [x] **Step 4: Directly verify clean startup**

  Run the dashboard, run `python manage.py demo --min 2 --max 2` twice, and
  inspect the SSE initial/current projection after the second run. Confirm only
  the second run's two entry IDs are present.

### Task 4: Record completion and validate

**Files:**
- Modify: `openspec/changes/add-asyncqueue-demo/tasks.md`

**Consumes:** Completed SSE transport and demo reset behavior.

**Produces:** Updated task text/checkboxes reflecting direct verification.

- [x] **Step 1: Record the SSE transport and reset work**

  Extend the publisher-worker task text to mention reset before publishing, and
  update the dashboard helper wording from polling to SSE. Mark only the tasks
  whose implemented requirements are now complete.

- [x] **Step 2: Run final direct verification and artifact validation**

  Run `python manage.py check`, verify the SSE stream and clean second run,
  then run:

  ```sh
  openspec validate add-asyncqueue-demo --strict
  git diff --check
  ```
