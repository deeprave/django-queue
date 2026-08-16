# Provider composition follow-up implementation plan

## Objective

Replace the initial universal claim/settle provider hierarchy with composed,
transport-specific delivery. Preserve the queue-facing producer, reader, and
administration APIs while ensuring that only backend-selected workers know how
their transport receives, acknowledges, retries, recovers, or prunes work.

## Target architecture

```text
application
    │ public queue APIs; handlers/listeners return or raise
    ▼
AsyncQueue / EventQueue
    │ compose a provider; no provider operations exposed
    ▼
RedisAsyncQueue / RedisEventQueue
    │ inject QueueProviderRedis and choose a Redis default worker
    ▼
Redis worker ───────────────► Queue-owned QueueProviderRedis delivery operations

Future transport queue ─────► its provider + native transport-aware worker
```

## Work sequence

1. Reduce `QueueProvider` to its initial lifecycle-only contract,
   `aclose()`, and remove provider inheritance that encodes Redis claim
   semantics. Do not place a clock in the protocol yet: promote it only after
   a second concrete provider demonstrates a shared transport-independent time
   contract. Redis delivery operations remain on the queue-owned Redis provider
   rather than becoming universal provider operations.
2. Refactor `AsyncQueue` and `EventQueue` into concrete facades constructed
   with a provider. Keep raw values, enqueue, lookup, direct dequeue, and
   terminal pruning at this layer. Remove queue lifecycle and ownership
   forwarding methods that exist only to relay to a provider.
3. Give each concrete backend an overridable default worker. Redis variants
   create Redis workers with their Redis provider; memory variants explicitly
   select the generic semantic workers. Validate worker/backend compatibility
   at construction.
4. Move Redis receive, claim, lease renewal, settlement, release, recovery,
   pruning, and lifecycle Pub/Sub behaviour to Redis workers plus
   `QueueProviderRedis`. Model the worker's private stable delivery identity
   inside its backend-owned delivery session rather than as a public argument.
   The queue never publishes its provider instance; that boundary lets workers
   call the provider's delivery methods directly without artificial method
   privacy.
5. Rework direct dequeue to use a self-contained provider receive/remove path
   with an ephemeral internal ownership context. Do not expose that context to
   callers.
6. Adapt event runtime and configured queue creation to instantiate each
   backend's default worker or an explicitly compatible override. Preserve the
   single runtime loop and queue-runtime identity guarantees.
7. Update README and API documentation to distinguish queue-facing operations,
   backend providers, and transport-specific workers. Remove obsolete public
   names rather than retaining compatibility aliases.

## Verification plan

- Add tests that handlers and listeners receive only immutable entries and
  cannot supply ownership values.
- Verify Redis workers retain existing claim/recovery/renewal and terminal
  lifecycle behaviour.
- Verify memory workers remain process-local and do not implement Redis-only
  delivery requirements.
- Verify a minimal hypothetical non-Redis provider can satisfy the common
  facade contract without claim/settle methods.
- Run Ruff, formatting, Ty, the full pytest suite including slow tests, Django
  demo checks, strict OpenSpec validation, and a fresh independent review
  cycle.
