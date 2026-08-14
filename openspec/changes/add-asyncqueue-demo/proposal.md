## Why

A runnable, deliberately small example makes the async Redis queue and passive
lifecycle observation API easier to understand than isolated backend tests.
It should demonstrate a dashboard and an independently running producer/worker
without requiring a database, authentication, or Django's admin application.

## What Changes

- Add `demo_aq/`, a standalone minimal Django dashboard project with its own
  `pyproject.toml`; it depends on the parent `django_queue` module and does not
  require a published package or other project dependency.
- Configure one Redis-backed AsyncQueue named `demo`, using Redis port 16379.
- Add one dashboard page that presents current retained queue entries and uses
  a queue observer to add, update, or remove rows as lifecycle snapshots arrive.
  The page provides a refresh control that replaces that observer and reloads
  the dashboard's local view.
- Add a management command that independently publishes sample entries from
  random `man -k .` output and metadata; Django's configured `runqueues`
  command runs the worker and handler separately.
- Add `compose.yaml` for starting Redis and the dashboard with the configured
  Redis port exposed as 16379.

## Capabilities

### New Capabilities

- `asyncqueue-demo-dashboard`: A database-free single-page Django dashboard
  that displays and live-updates retained entries for the `demo` AsyncQueue.
- `asyncqueue-demo-publisher-worker`: A publisher command and configured
  Django worker that generate and process timed demo tasks.

### Modified Capabilities

None.

## Impact

Adds an isolated `demo_aq/` example project, Docker Compose startup support,
and demo-only Django settings, template, static helper, and management command.
It does not change the queue library's public behavior or require a database,
auth, or admin setup.
