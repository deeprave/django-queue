## 1. Lifespan contract tests

- [ ] 1.1 Write failing ASGI lifespan tests for startup, graceful shutdown, and
  startup failure using in-memory queues and explicit handlers.
- [ ] 1.2 Add a regression test that Django app configuration alone does not
  create an ASGI process worker.

## 2. ASGI process-worker integration

- [ ] 2.1 Implement the opt-in Django ASGI lifespan wrapper and its explicit
  handler and queue-mapping API.
- [ ] 2.2 Start one `AsyncQueueWorker` on lifespan startup, and cooperatively
  cancel and await it before lifespan shutdown completes.
- [ ] 2.3 Log unexpected post-startup worker failure without silently creating
  a replacement worker.
- [ ] 2.4 Log the required production-use warning on successful worker startup.

## 3. Documentation and verification

- [ ] 3.1 Document Django ASGI integration, explicit handler registration, and
  the process-local in-memory queue boundary.
- [ ] 3.2 Run the complete Python 3.14 test suite, Ruff, and strict OpenSpec
  validation; refactor tests only where it improves their clarity or removes
  genuine duplication.
