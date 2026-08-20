# Priority Queue Demo

This is a deliberately stripped-down Django application that demonstrates how
`django_queue`'s priority-variant queue dispatches entries by priority rather
than plain arrival order. It has no database, authentication, or admin
application: Redis is the only service it needs. It mirrors `demo_aq`
(the plain async-queue demo) closely; the difference is entirely in the
queue backend and the worker's injection scenario.

`manage.py demo` is only a seed-data command for this application. It clears
the demo queue and enqueues Faker-generated entries, spread randomly across
four priority tiers, with artificial delays and failures so the dashboard has
something to show. Do not use it as a production publishing pattern;
production code should enqueue its real work through the configured queue
API.

## Run it

Run Redis for the local dashboard and worker:

```sh
docker compose up -d
```

Run the dashboard from this checkout so source changes reload without rebuilding
an image:

```sh
uv run python manage.py runserver
```

In another terminal, start Django's configured queue worker:

```sh
uv run python manage.py runqueues
```

`runqueues` alone is enough -- it seeds one entry per priority tier on
startup if the queue is empty (see "How it works" below), and its
injectors take over from there. Optionally publish an extra random batch
at any point:

```sh
uv run python manage.py demo --min 6 --max 16
```

Redis is exposed only at `127.0.0.1:16389` (a different port than `demo_aq`'s
`16379`, so both demos can run side by side). The optional containerised
dashboard is also localhost-only, on port `8001`:

```sh
docker compose --profile dashboard up -d
```

## How it works

The queue is configured with `RedisAsyncPriorityQueueJson`
(`django_queue.backends.redis`), not the plain `RedisAsyncQueueJson` that
`demo_aq` uses. Every entry still moves through the same
`queued → running → succeeded/failed` lifecycle; what differs is the order
`runqueues` dispatches them in.

`runqueues`'s worker-activation loop waits for the queue to already have a
pending entry before it ever constructs the worker and calls its `run()`
-- but this worker's per-tier injectors live inside `run()` itself, so a
genuinely empty queue would otherwise deadlock forever: the worker never
starts because nothing is pending, and nothing becomes pending because
only the worker's own injectors would populate it. `DashboardConfig.ready()`
seeds one entry per priority tier the moment `manage.py runqueues` starts
(guarded by `ahas_pending()`, so it's a no-op if the queue already has
entries), breaking that deadlock without needing `manage.py demo` run
first.

The demo worker (`DemoPriorityQueueWorker`) runs four independent injector
tasks, one per priority tier, each on its own random interval:

| Tier      | Priority | Cadence       | Queued → running | Colour |
| --------- | -------- | ------------- | ----------------- | ------ |
| `low`     | `-5`     | every 22–32s  | 25–32s             | grey   |
| `normal`  | `0`      | every 26–40s  | 17–23s             | blue   |
| `high`    | `5`      | every 45–55s  | 10–15s             | amber  |
| `urgent`  | `10`     | every 65–85s  | 4–8s               | red    |

An entry's full visible lifetime (queued delay + running delay + dashboard
retention) averages ~95 seconds, so the injection cadence is tuned to keep
each tier's steady-state row count in the 3–5 range for low/normal.
High/urgent are deliberately rarer than low/normal (~1.5x and ~2.5x the
interval), but not so much rarer that they read as absent over a short
demo session -- an earlier ~3x/~5x gap did exactly that.

The queued → running wait is scaled by priority tier into narrow,
non-overlapping bands, highest priority shortest. This matters beyond
display: `RedisAsyncQueueWorker`'s dispatch loop claims the highest-priority
pending entry, and if that entry's own wait hasn't elapsed yet, releases it
and moves on rather than dispatching it early -- so if the queued → running
wait were the same random range for every tier regardless of priority, a
low-priority entry could still happen to become "due" and start running
before a higher-priority entry still waiting out its own delay, even though
the queue always *claims* in strict priority order. Scaling the wait by
tier closes that gap, so dispatch order tracks priority order all the way
through, not just the underlying claim. The running → terminal wait is
deliberately NOT scaled -- once dispatched, how long a handler takes has
nothing to do with how urgently it was picked up.

At most 5 handlers run concurrently (`DemoPriorityQueueWorker`'s
`_MAX_CONCURRENT_HANDLERS`). Without a cap, a steady drip of low/normal
entries can keep an unbounded number of handlers running, drowning out the
rare high/urgent arrivals in a wall of low-priority `running` rows. The cap
is enforced in `_next`, *before* claiming: if all 5 slots are busy, the
worker doesn't claim anything that cycle, leaving every pending entry
visibly `queued` and claimable the instant a slot frees, rather than
claiming an entry and leaving it idle mid-lifecycle while it waits for a
slot. Combined with the priority-scaled queued → running wait above, this
means the 5 running slots are consistently filled by the highest-priority
entries currently due, not just whichever arrived first.

The dashboard always lists entries highest-priority first, with arrival
order as the tie-break within a tier — the same order the priority queue
itself dispatches in. Low and normal entries arrive often, forming a steady
backlog underneath. High and urgent entries are rare, so watching the
dashboard you can see them appear at the top of the table immediately on
arrival and move to `running` well ahead of older, lower-priority entries
still waiting below — and see low-priority entries sit near the bottom,
still queued, for a while when higher tiers keep arriving, which is the
other half of what a priority queue is for.

Critically, the worker claims and releases entries through the queue's own
`aclaim`/`arelease` methods (`queue.aclaim(...)`, `queue.arelease(...)`),
not the provider directly. `RedisAsyncPriorityQueue` overrides those methods
to claim from and release back to its priority-ordered pending store;
calling the provider's `aclaim`/`arelease` directly (as `demo_aq`'s worker
does, since it only needs the plain FIFO path) would silently bypass
priority ordering altogether, including on the release path -- a released,
not-yet-due entry must not be able to jump ahead of a genuinely
higher-priority entry still waiting, and only routing through the queue's
own hooks guarantees that.

The dashboard shows a "Priority" column with a coloured badge naming each
entry's tier and numeric priority, styled the same way the existing "State"
column already is.

The dashboard does not read Redis directly. It subscribes to the `demo`
queue through `queue_observer`, which first supplies retained-entry
snapshots and then receives lifecycle snapshots as the worker publishes
them. The dashboard keeps a small in-process projection of those snapshots
and streams updates to the browser with Server-Sent Events.

Completed entries are retained for 30 seconds. Pruning then publishes an
observer-only `terminated` snapshot, which removes the corresponding
dashboard row.

Each `demo` run replaces the existing `demo` queue state. Use the dashboard
Refresh button after starting a new batch to create a fresh observer
projection.
