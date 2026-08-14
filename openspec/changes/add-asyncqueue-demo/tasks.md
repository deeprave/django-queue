## 1. Demo project foundation

- [x] 1.1 Create the self-contained `demo_aq/` Django project, its own
  `pyproject.toml`, minimal settings, and a single Redis AsyncQueue alias named
  `demo` at port 16379 without database, auth, or admin configuration.
- [x] 1.2 Add the demo Compose setup so Redis is exposed on host port 16379 and
  the optional dashboard container is isolated in its own profile.
- [x] 1.3 Add concise startup documentation for the dashboard, publisher, and
  independent `runqueues` worker commands.

## 2. Dashboard capability

- [x] 2.1 Move retained-entry enumeration from the public `BaseQueue` API to
  an internal `AsyncQueue` observer-bootstrap capability, with no EventQueue
  equivalent.
- [x] 2.2 Implement the one-page retained-entry dashboard and its minimal SSE
  browser helper/projection endpoint, showing entry ID, state, payload metadata,
  and queue timeout.
- [x] 2.3 Start a `demo` queue observer for the local dashboard service and
  apply its snapshots to add, update, and remove dashboard rows without
  mutating queue entries.
- [x] 2.4 Add the `Refresh` control that replaces the dashboard observer and
  projection through a full page reload, with direct dashboard verification.

## 3. Publisher-worker capability

- [x] 3.1 Configure the `demo` queue's dotted-path handler and custom worker
  so Django's `runqueues` command independently runs the demo processor.
- [x] 3.2 Implement the management command's independent task publisher using
  random `man -k .` messages and additional metadata, clearing
  prior `demo` queue state before publishing.
- [x] 3.3 Implement the visible 10–30 second queued and 30–60 second running
  demo lifecycle, including one controlled failure in 6–9 entry batches and
  two controlled failures in 10–16 entry batches.
## 4. Verification

- [x] 4.1 Deliberately omit automated demo coverage at the user's direction and
  verify the dashboard and worker directly.
- [x] 4.2 Run the demo's Django checks, lint, direct lifecycle smoke checks,
  and strict OpenSpec validation; parent-project tests are intentionally out of
  scope for this demo slice.
