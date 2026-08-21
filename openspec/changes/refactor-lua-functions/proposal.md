## Why

Redis provider operations currently use many isolated EVALSHA scripts. Shared
logic, especially scheduled-entry promotion, must be copied into each script,
which permits claim and direct-dequeue paths to diverge. Redis 7 has supported
persistent Function libraries for years and Redis 8 is the deployment target.

## What Changes

- Replace the provider's registered EVALSHA scripts with a versioned Redis
  Function library invoked through FCALL.
- Put reusable Lua helpers, including scheduled promotion, record/index
  cleanup, and priority-score handling, inside that library.
- Load or replace the library idempotently when a provider establishes its
  Redis client, while retaining clear failures for Redis versions or ACLs that
  cannot support functions.
- Keep each public provider operation a single atomic FCALL; helpers are local
  calls within that function, never a chain of client-side FCALLs.
- Correct Redis path divergence so equivalent claim and direct-dequeue
  operations preserve the same eligibility, uniqueness, and cleanup rules.

## Capabilities

### New Capabilities

- `redis-function-library`: Redis Function library deployment, invocation, and
  compatibility requirements for the Redis provider.

### Modified Capabilities

- `redis-entry-claims`: preserve atomic single-entry dispatch semantics across
  Redis claim and direct-dequeue paths.

## Impact

- `django_queue.backends.redis.provider` and its Redis integration tests.
- Redis deployment requires version 7 or later and ACL access to FUNCTION LOAD
  and FCALL.
- Existing EVALSHA script cache entries are unaffected during migration.
