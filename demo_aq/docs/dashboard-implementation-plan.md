# AsyncQueue Dashboard Display Implementation Plan

> **For agentic workers:** Execute this plan inline in the current workspace.
> Do not create a worktree or subagents.

**Goal:** Render and live-update a retained-entry table from `queue_observer`
snapshots for the configured `demo` queue.

**Architecture:** A `DashboardProjection` owns process-local, JSON-ready entry
rows and its `QueueSubscription`. Its observer callback seeds and updates the
projection; Django exposes it through a small JSON view. The page contains an
inline polling script that upserts table rows from that endpoint.

**Tech Stack:** Django views/templates, `django_queue.queue_observer`, Redis
observer transport, browser `fetch` and DOM APIs.

## Global Constraints

- The dashboard projection MUST receive queue data only through
  `queue_observer("demo", callback)`; it MUST NOT enumerate Redis directly.
- Do not use WebSockets, Channels, a database, or static assets.
- Keep terminal rows while their entries remain retained in Redis.
- Table columns are ID, state, message, metadata/source, queued, finished, and
  timeout.
- The script lives inline in `dashboard/templates/dashboard/index.html`.
- This demo deliberately skips automated tests; verify the running services
  directly instead.

---

### Task 1: Add the observer-backed dashboard projection

**Files:**
- Create: `demo_aq/dashboard/projection.py`
- Modify: `demo_aq/dashboard/apps.py`

**Consumes:** `django_queue.queue_observer(queue_name, callback)` and its
`QueueSubscription.unsubscribe()` API; immutable `QueueEntry` snapshots.

**Produces:** `DashboardProjection.start()`, `DashboardProjection.refresh()`,
and `DashboardProjection.rows()` for the local `demo` observer.

- [x] **Step 1: Create `DashboardProjection` with a lock-protected dictionary**

  Store rows by `str(entry.id)`. Each callback converts a snapshot to a
  JSON-ready dictionary with these exact keys:

  ```python
  {
      "id": str(entry.id),
      "state": entry.status.value,
      "message": entry.payload.get("message", ""),
      "metadata": entry.payload,
      "queued_at": entry.queued_at.to_timestamp(),
      "finished_at": (entry.finished_at.to_timestamp() if entry.finished_at else None),
      "timeout_seconds": entry.timeout_seconds,
  }
  ```

  `rows()` returns a copy sorted by `queued_at` then `id`. The callback always
  replaces an ID's row, including terminal snapshots.

- [x] **Step 2: Subscribe on the first dashboard request**

  `start()` is idempotent: it does nothing while an active subscription exists;
  otherwise it calls `queue_observer("demo", self.update)` and retains the
  returned subscription. Call it from the dashboard page and state views so
  the independent worker process never creates a dashboard observer.

- [x] **Step 3: Implement refresh lifecycle**

  `refresh()` unsubscribes the current subscription when present, clears the
  row dictionary while locked, and calls `start()` for a new bootstrap batch.
  This changes only presentation state and never invokes queue mutation APIs.

- [x] **Step 4: Directly verify observer bootstrap and updates**

  With Redis running, launch the local dashboard and run `python manage.py
  demo --min 1 --max 1` in another terminal. Confirm `rows()` receives the
  queued, running, and succeeded snapshots and retains the final row.

### Task 2: Expose projection rows through Django views

**Files:**
- Modify: `demo_aq/dashboard/views.py`
- Modify: `demo_aq/dashboard/urls.py`

**Consumes:** The module-level projection from `dashboard.projection`.

**Produces:** `GET /state/` returning `{"entries": [...]}` and `POST
/refresh/` that resets the observer projection before redirecting to `/`.

- [x] **Step 1: Render the table page**

  Keep `index(request)` as the template view. It does not read queue entries
  and only renders the table shell.

- [x] **Step 2: Add the state endpoint**

  Add `state(request)` using `JsonResponse({"entries": projection.rows()})`.
  It accepts only `GET`; Django returns a 405 for other methods.

- [x] **Step 3: Add the refresh endpoint**

  Add `refresh(request)` using `require_POST`. It calls `projection.refresh()`
  and returns `redirect("index")`. Route it as `refresh/`; the form in the
  template supplies Django's CSRF token.

- [x] **Step 4: Directly verify JSON and refresh**

  Request `http://127.0.0.1:8000/state/` and confirm it returns JSON only from
  the observer projection. Submit Refresh and confirm the page reloads and the
  state endpoint repopulates from the new observer bootstrap.

### Task 3: Build the inline table updater

**Files:**
- Modify: `demo_aq/dashboard/templates/dashboard/index.html`

**Consumes:** `GET /state/` response entries with the keys defined in Task 1.

**Produces:** A table body where each row has `data-entry-id`, and an inline
polling script that inserts or updates rows without a full page reload.

- [x] **Step 1: Replace the placeholder text with table markup**

  Add a small Refresh form and a `<table>` with headings `ID`, `State`,
  `Message`, `Metadata`, `Queued`, `Finished`, and `Timeout`. Give the body
  `id="entries"`.

- [x] **Step 2: Add the inline polling script**

  Fetch `/state/` every second after an immediate initial request. For each
  entry, locate `tr[data-entry-id="..."]`; create it if absent, otherwise
  replace its cell text. Render timestamps with
  `new Date(seconds * 1000).toLocaleString()`, blank finished values as `—`,
  blank timeouts as `—`, and metadata with `JSON.stringify(entry.metadata)`.
  Use `textContent` for every value.

- [x] **Step 3: Preserve terminal rows and handle transient polling errors**

  Do not remove rows merely because their state is terminal. If a polling
  request fails, log one `console.error` and allow the next interval to retry;
  keep currently displayed rows unchanged.

- [x] **Step 4: Directly verify browser behavior**

  Open `http://127.0.0.1:8000/`, start `python manage.py demo --min 2 --max
  2`, and confirm entries appear without a page reload. Confirm their existing
  rows change state through completion and gain finished timestamps.

### Task 4: Align the change artifacts and startup documentation

**Files:**
- Modify: `openspec/changes/add-asyncqueue-demo/tasks.md`
- Create: `demo_aq/README.md`

**Consumes:** The completed table, observer, state endpoint, and Compose
Redis-only default.

**Produces:** Checked OpenSpec dashboard tasks and concise commands for local
Redis, dashboard, worker, and the optional Compose dashboard profile.

- [x] **Step 1: Write the demo startup commands**

  Document these exact normal-development commands:

  ```sh
  docker compose -f compose.yaml up -d
  uv run python manage.py runserver
  uv run python manage.py demo --min 12 --max 16
  ```

  Also document the optional container dashboard command:

  ```sh
  docker compose -f compose.yaml --profile dashboard up -d
  ```

- [x] **Step 2: Check the dashboard tasks completed by this plan**

  Mark 2.2, 2.3, and 2.4 complete in `tasks.md` after the direct verification
  steps succeed. Do not mark the focused-coverage task, because the user has
  explicitly chosen to skip demo automated tests.

- [x] **Step 3: Run final direct verification**

  Start Redis with plain Compose, run the dashboard locally, run the demo
  worker, and load the page. Confirm the table displays retained entries and
  updates existing rows through terminal status. Run `git diff --check` before
  handoff.
