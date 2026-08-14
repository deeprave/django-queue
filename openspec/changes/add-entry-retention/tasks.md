## 1. Terminal entry retention

- [ ] 1.1 Write failing tests for terminal expiry, non-terminal preservation,
  and observer delivery of an immutable `terminated` copy before removal.
- [ ] 1.2 Add retention configuration and explicit backend cleanup behavior,
  including the AsyncQueue observer-only termination event before durable
  deletion.
- [ ] 1.3 Document status lookup lifetime and observer removal semantics, then
  run the full suite.
