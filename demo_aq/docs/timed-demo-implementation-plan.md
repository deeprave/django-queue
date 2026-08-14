# Timed AsyncQueue Demo Implementation Plan

> **For agentic workers:** Execute inline in the current workspace. Do not create a worktree, commit, or add automated tests for this demo.

**Goal:** Make each demo batch visibly progress through queued, running, and terminal states, with a small controlled sample of failures and correctly aligned state text.

**Architecture:** `manage.py demo` will select failing payloads before enqueueing them. Every payload will carry a list of timestamped state transitions. Django settings will configure a demo-only `AsyncQueueWorker` subclass and handler for `manage.py runqueues`; the worker dispatches due queued entries serially while rotating not-yet-due entries, and every dispatch spawns an independent handler that waits for and reports its terminal transition. The inline dashboard script will place ID and state content inside spans, leaving table cells as normal table cells.

**Tech Stack:** Django management command, django-queue AsyncQueueWorker, asyncio, inline JavaScript, Pico CSS CDN.

## Global Constraints

- Preserve the existing uncommitted `demo_aq/` and OpenSpec change work.
- Use the existing management command name: `python manage.py demo`.
- Keep batch size options as `--min` and `--max`, defaulting to 6 and 16.
- Do not add or run automated tests for this demo; use direct Django validation only.
- Do not create a separate worktree, commit, or stage changes.

---

### Task 1: Add timed lifecycle and controlled failures

**Files:**
- Modify: `demo_aq/dashboard/management/commands/demo.py`
- Modify: `openspec/changes/add-asyncqueue-demo/specs/asyncqueue-demo-publisher-worker/spec.md`

**Interfaces:**
- Consumes: queued demo payloads containing `message`, `source`, and `should_fail`.
- Produces: `DemoQueueWorker._next_entry(queue)`, which rotates entries until
  their 10–30 second queued transition is due; a separately spawned
  `_handle_entry(entry)`, which completes 30–60 seconds later with success or
  a controlled error.

- [x] **Step 1: Set the command minimum default to six entries**

Set `--min` to `default=6` and update its help text to say `default: 6`.

- [x] **Step 2: Mark one or two payloads for failure before enqueueing**

Build payloads with a `should_fail` boolean selected by `random.sample`: one failing index for batches of 6–9 entries and two for batches of 10–16 entries.

- [x] **Step 3: Schedule the queued state and concurrently run handlers**

```python
payload = {
    "transitions": [
        {"at": queued_at + random.uniform(10, 30), "state": "running"},
        {"at": terminal_at, "state": "succeeded"},
    ],
}

class DemoQueueWorker(AsyncQueueWorker):
    async def _next_entry(self, queue):
        entry = await queue.adequeue_entry()
        if _transition_due(entry, QueueEntryStatus.RUNNING):
            return entry, None
        await _requeue_entry(queue, entry)
        return None

    async def _dispatch(self, queue, handler, entry, lease_seconds=None):
        running_entry = await queue.amark_running(entry.id)
        await queue.apublish_lifecycle_snapshot(running_entry)
        asyncio.create_task(self._complete_entry(queue, handler, running_entry))
```

- [x] **Step 4: Configure `DemoQueueWorker` and its handler for `runqueues`**

Set `WORKER` and `HANDLER` to their dotted paths in `QUEUES["demo"]`. Keep
`manage.py demo` publisher-only and use Django's `manage.py runqueues` to
create the configured worker.

### Task 2: Correct table-cell structure and state colours

**Files:**
- Modify: `demo_aq/dashboard/templates/dashboard/index.html`
- Modify: `demo_aq/docs/dashboard-design.md`

**Interfaces:**
- Consumes: SSE entry snapshots with `id`, `state`, and timing fields.
- Produces: one stable six-cell table row per entry, with ID and state spans inside their respective cells.

- [x] **Step 1: Keep each table cell as a table cell**

Create inner `span` elements for the ID and state values and update those spans in `render(entry)`. Do not apply `.entry-id` or `.state` directly to a `td`.

- [x] **Step 2: Use foreground-only state styling**

Remove state badge padding, backgrounds, and rounded corners. Apply blue to queued, yellow to running, green to succeeded, and red to failed state spans.

- [x] **Step 3: Document the presentation and lifecycle contract**

Describe the table-cell spans, foreground state colours, 10–30 second queued delay, 30–60 second running delay, and controlled one/two failure distribution.

### Task 3: Direct validation

**Files:**
- Verify only: `demo_aq/dashboard/management/commands/demo.py`
- Verify only: `demo_aq/dashboard/templates/dashboard/index.html`

- [x] **Step 1: Run Django configuration validation**

Run `uv run --project demo_aq python demo_aq/manage.py check`.

- [x] **Step 2: Inspect the resulting diff**

Run `git diff --check` and review the management command and template changes. Automated tests are intentionally omitted at the user's request.

- [x] **Step 3: Run a direct six-entry lifecycle smoke check**

Run `uv run --project demo_aq python demo_aq/manage.py demo --min 6 --max 6`
against the local Redis container. Inspect the retained entries to confirm
overlapping running states, five successes, and one controlled failure, then
interrupt the persistent worker with Ctrl-C.
