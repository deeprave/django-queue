## 1. Function library foundation

- [ ] 1.1 Define a namespaced, versioned Redis Function library source and its
  shared Lua helper conventions.
- [ ] 1.2 Add async Redis client support to load, replace, and invoke the
  library idempotently with actionable version and ACL errors.
- [ ] 1.3 Add test fixtures for isolated function-library installation and
  loading failures.

## 2. Provider migration

- [ ] 2.1 Port shared scheduling promotion and priority-score helpers into the
  library and reuse them from claim and direct-dequeue functions.
- [ ] 2.2 Port tracked-entry enqueue, claim, lease, release, recovery, delete,
  and lifecycle functions from EVALSHA to FCALL.
- [ ] 2.3 Port raw-value and event-queue scripts, then remove obsolete script
  registration and cache handling.

## 3. Validation and deployment safety

- [ ] 3.1 Add parity tests for every migrated provider operation, including
  concurrent promotion and failure/cleanup paths.
- [ ] 3.2 Verify Redis Function persistence, replacement, key declaration, and
  ACL/version failure behaviour.
- [ ] 3.3 Run the focused Redis suite, full lint/format/type/test suite, and
  strict OpenSpec validation.
