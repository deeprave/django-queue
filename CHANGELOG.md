# Changelog

## v1.0.2 — 2026-08-19

- `queue_observer` now supports decorator syntax (`@queue_observer("alias")`) alongside the existing direct call, deferring backend activation until the shared runtime starts.
- Fixed a bug where two threads touching the same configured `AsyncQueue` alias for the first time could each get their own lifecycle-observer receiver and Redis connection, instead of sharing one. Event workers and observer receivers for all configured queues now run on a single shared background thread, started once at process startup. No API changes; not a breaking change.

## v1.0.1 — 2026-08-17

- Fixed the Redis queue observer to run fully asynchronously, matching the rest of the Redis backend. No API changes; not a breaking change.

## v1.0.0 — 2026-08-17

- Initial release
