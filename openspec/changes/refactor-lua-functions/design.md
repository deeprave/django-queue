## Context

`QueueProviderRedis` currently registers a collection of EVALSHA scripts per
async Redis client and stores their handles in `_Scripts`. This works but makes
shared atomic behaviour expensive to maintain: each script has an isolated Lua
context, so scheduled promotion and index cleanup must be copied into every
claim and dequeue path.

## Goals / Non-Goals

**Goals:**

- Use one versioned Redis Function library with local reusable Lua helpers.
- Preserve existing provider operation names, queue-facing behaviour, atomicity,
  and Redis key contracts.
- Ensure the provider idempotently installs the library before its first FCALL.
- Make Redis 7 the supported minimum; Redis 8 is the deployment target.

**Non-Goals:**

- Change queue lifecycle, priority, scheduling, or raw-value semantics.
- Retain an EVALSHA fallback for pre-Redis-7 servers.
- Expose the function library as a public extension API.

## Decisions

### Ship one namespaced library with timestamp build metadata

The provider owns a stable library name such as `django_queues`, with stable,
namespaced registered entry points. Its source carries a `YYMMDD_HHMMSS` build
timestamp as version metadata for inspection and diagnostics. Upgrades use
`FUNCTION LOAD REPLACE` against the stable library name. Function names are
global across libraries, so timestamping library names would create collisions
unless every caller and function name also migrated.

Alternative: one library per queue. Rejected because libraries are server
deployment objects, while queue names are application data and can be dynamic.

### Keep one public operation within one FCALL

Python invokes one registered function for each provider mutation. Helpers are
ordinary local Lua functions within the library; Python must not compose
multiple FCALLs to form one provider operation. This preserves the current
atomic operation boundary.

### Install idempotently per client initialisation

When creating an async Redis client, the provider loads the bundled library
with replacement only when necessary, then records its availability for that
event loop. Loading failures report the Redis version or ACL cause clearly.
Function invocation passes all accessed key names explicitly, retaining Redis
Cluster key-declaration discipline.

### Validate parity before removing scripts

Port one operation at a time behind provider-level tests, including failure and
concurrency cases. Remove script registration only after every current entry
point uses the function library.

## Risks / Trade-offs

- [Library deployment requires ACL changes] → Document FUNCTION LOAD and FCALL
  permissions and fail with actionable backend configuration errors.
- [A replacement is incompatible with live callers] → Version functions and
  keep entry-point signatures stable through the migration.
- [Function execution blocks Redis] → Retain short, bounded operations and
  existing key/argument discipline.

## Migration Plan

1. Add the function library and provider loading/invocation support.
2. Port and test all current scripts while preserving their public operations.
3. Deploy to Redis 7+ with FUNCTION LOAD/FCALL ACL permissions.
4. Roll back application code by restoring the previous provider version;
   loaded function libraries do not affect existing EVALSHA calls.
