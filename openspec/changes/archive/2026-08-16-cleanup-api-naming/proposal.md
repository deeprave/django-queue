## Why

The queue API has accumulated redundant nouns and long prefixes or suffixes
that obscure the operation being performed. The event-queue and provider work
also makes it timely to establish one concise, consistent naming vocabulary
before the package is released.

## What Changes

- Audit public queue, worker, provider, registry, and lifecycle APIs for
  redundant `_entry`, queue, worker, and transport-specific naming.
- **BREAKING** Rename redundant public and internal operations to concise names
  that describe the action in their established context.
- Retain qualifiers only where they distinguish genuinely different operations,
  such as raw queue values from retained lifecycle records.
- Update documentation, configuration examples, type contracts, and OpenSpec
  requirements to use the agreed vocabulary exclusively.
- Do not retain compatibility aliases or deprecated spellings for renamed APIs.

## Capabilities

### New Capabilities

- `api-naming`: Defines the concise naming rules and canonical operation names
  across queue, worker, and provider APIs.

### Modified Capabilities

- `async-queue-backends`: Updates the asynchronous and synchronous backend
  contract to use the canonical operation names.
- `configured-queue-registry`: Updates registry-facing API and configuration
  terminology where the canonical naming rules apply.

## Impact

This is a deliberately breaking API cleanup affecting queue backends, workers,
providers, tests, examples, documentation, type annotations, and the existing
OpenSpec contracts. No dependencies or compatibility layer are introduced.
