## Context

An entry must not be visible to two workers, and acknowledgement must identify
the worker that owns the claim.

## Goals / Non-Goals

**Goals:** Atomic pending-to-claimed transition and owner-checked acknowledgement.

**Non-Goals:** Expiry, redelivery, retries, or at-least-once guarantees.

## Decisions

Redis scripts atomically move an ID from pending storage to a claimed record
holding worker ID and claim time. Acknowledgement removes it only for its owner.
The API is additive; workers switch in the later lease-recovery change.

## Risks / Trade-offs

- [Crashes leave claims] → deliberately resolved by the follow-up lease change.
