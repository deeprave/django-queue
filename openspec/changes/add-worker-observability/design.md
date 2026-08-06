## Context

The worker has lifecycle state but no stable identity or operational snapshot.

## Goals / Non-Goals

**Goals:** Supply a generated worker ID and in-process metrics.

**Non-Goals:** Metrics exporters, a dashboard, or distributed liveness consensus.

## Decisions

Each worker gets a UUIDv7 identifier and exposes an immutable snapshot with
running state, start time, active entry ID, and success/failure counts.

## Risks / Trade-offs

- [Snapshot is process-local] → document that monitoring collects it externally.
