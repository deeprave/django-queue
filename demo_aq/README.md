# Async Queue Demo

This is a deliberately stripped-down Django application that demonstrates how
`django_queue` queues, workers, handlers, and observers work together in
practice. It has no database, authentication, or admin application: Redis is
the only service it needs.

`manage.py demo` is only a seed-data command for this application. It clears
the demo queue and enqueues Faker-generated entries with artificial delays and
failures so the dashboard has something to show. Do not use it as a production
publishing pattern; production code should enqueue its real work through the
configured queue API.

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

Then publish a random batch:

```sh
uv run python manage.py demo --min 6 --max 16
```

Redis is exposed only at `127.0.0.1:16379`. The optional containerised
dashboard is also localhost-only:

```sh
docker compose --profile dashboard up -d
```

## How it works

The `demo` command seeds a random batch of Faker-generated messages. Each entry starts `queued`, waits
10–30 seconds before it can be dispatched, then waits another 30–60 seconds in
its handler before reaching `succeeded` or, with a one-in-eight random chance,
`failed`.

`runqueues` is Django Queue's standard worker command. It reads the `demo`
queue's `WORKER` and `HANDLER` settings, starts the configured worker, and
dispatches due entries in order. Each dispatched entry is marked `running` and
its handler runs independently, so later due entries can be dispatched while
earlier handlers are still waiting.

The demo queue worker runs one process-local injector. It enqueues a new entry
with the same normal lifecycle every random 5–20 seconds and cancels that task
when the worker stops.

The dashboard does not read Redis directly. It subscribes to the `demo` queue
through `queue_observer`, which first supplies retained-entry snapshots and
then receives lifecycle snapshots as the worker publishes them. The dashboard
keeps a small in-process projection of those snapshots and streams updates to
the browser with Server-Sent Events. As queued entries become running and then
finish, observers receive that progress information and the corresponding row
is updated.

Completed entries are retained for 30 seconds. Pruning then publishes an
observer-only `terminated` snapshot, which removes the corresponding dashboard
row.

Each `demo` run replaces the existing `demo` queue state. Use the dashboard
Refresh button after starting a new batch to create a fresh observer projection.
