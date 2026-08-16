## Context

The package exposes raw queue operations, retained-record operations, worker
orchestration, configured registry access, and provider primitives. Names were
introduced incrementally, leaving redundant `_entry` suffixes and repeated
domain prefixes alongside newer concise provider primitives. The package is
unreleased, so no compatibility layer is required or desired.

## Goals / Non-Goals

**Goals:**

- Establish one short, context-aware naming vocabulary.
- Remove redundant target nouns and implementation prefixes from public and
  private APIs.
- Preserve a qualifier only when two operations would otherwise collide or
  become ambiguous in the same API surface.
- Apply the vocabulary consistently to Python APIs, configuration-facing
  terminology, documentation, type contracts, tests, and OpenSpec.

**Non-Goals:**

- Changing queue delivery, retention, claim, or worker semantics.
- Preserving aliases, deprecation shims, or compatibility wrappers.
- Renaming persisted Redis key formats, entry fields, or Django settings unless
  the API inventory identifies a user-facing naming conflict.

The documented Django settings remain `BACKEND`, `LOCATION`, `HANDLER`,
`WORKER`, `ENTRY_CLASS`, `TIMEOUT`, and `RETENTION_TIMEOUT`: they are explicit
configuration names, not redundant callable names. A configured queue's name
comes only from its `QUEUES` mapping alias; `queue_name` is never a supported
setting.

## Decisions

### Audit the complete surface before choosing individual replacements

Create an explicit rename inventory grouped by queue API, worker API, provider
API, registry/configuration API, and documentation. Every candidate records its
current name, canonical replacement, context, and whether a qualifier remains
necessary. This avoids mechanically removing `_entry` where a raw-value method
with the same name already exists.

Alternative: rename only the methods encountered during the event-queue work.
This would perpetuate inconsistent names elsewhere and does not meet the
change's purpose.

### Treat provider ownership as internal implementation detail

Provider claim, renew, release, remove, recovery, and settlement primitives
remain private queue/worker implementation hooks. Public queue APIs do not
accept or expose worker identifiers, claim owners, provider instances, or
transport-specific terms.

Alternative: expose ownership parameters on public dequeue methods. This would
leak worker coordination into normal application use and contradict the queue
abstraction.

### Prefer action names within a known context

Where an interface operates exclusively on lifecycle records, use the action
without an `_entry` suffix. Where one interface offers both raw queue values and
lifecycle records, retain or introduce the smallest qualifier that prevents an
ambiguous name collision. The inventory, rather than a blanket text rewrite,
decides each case.

### Make the rename a clean breaking cutover

Remove the old spellings from source, tests, documentation, and specs in the
same change. Do not provide aliases, fallback imports, deprecation warnings, or
dual configuration keys.

### Adopt the agreed canonical vocabulary

The public lifecycle-record vocabulary is `find` / `afind`, `dequeue` /
`adequeue`, `has_pending` / `ahas_pending`, and `prune` / `aprune`. `alist`
remains the retained-record collection operation, while `apublish` names the
worker-to-observer publication of one immutable record. Raw queue-value
operations remain `add`, `get`, `poll`, `peek`, `size`, and `clear`, because
they operate on a different domain from retained lifecycle records.

Provider primitive names similarly use the shortest established action name;
the provider remains an internal resource and is not exposed by queue facades.
The configured registry is `QueueRegistry`, a callable queue handler is
`Handler`, and worker activation metadata is `WorkerActivation`.

`QueueEntryNotFoundError` and `QueueEntryMissingError` remain distinct. The
former reports an application lookup or prune request for an absent retained
record; the latter is an internal worker/provider recovery condition for a
claimed identifier whose backing record disappeared.

### Document semantic queue types before storage backends

README documentation first distinguishes `AsyncQueue` from `EventQueue`.
Async queues retain lifecycle records and are dispatched by async handlers and
workers; event queues deliver transient events to registered listeners. Memory
and Redis are then presented as storage/delivery backend choices within those
semantic queue types. The configuration examples place one minimal async queue
and one minimal event queue side by side.

## Risks / Trade-offs

- [A mechanical rename can hide a semantic collision] → Require the inventory
  and focused tests for raw-value and lifecycle-record APIs before changing
  names.
- [Public examples can lag behind code] → Search and update README, demo,
  docs, OpenSpec, and test fixtures as part of the cutover.
- [Custom backend contracts can become unclear] → Update the formal backend
  requirements and custom-backend examples at the same time.

## Migration Plan

1. Agree the inventory and canonical names before implementation.
2. Rename the contracts and implementations atomically, with no aliases.
3. Update all in-repository consumers and documentation.
4. Run the full validation suite and strict OpenSpec validation.

There is no runtime migration or rollback compatibility path because the
package is unreleased. Reverting the change restores the prior source API.

## Open Questions

- Which retained-record operations need a qualifier because the raw queue API
  already owns the concise action name?
- Should the naming inventory include exception and type names, or only
  callable/configuration interfaces in this change?
