# Async Queue Demo

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

The dashboard shows retained `demo` queue entries from its `queue_observer`
subscription. Redis is exposed only at `127.0.0.1:16379`.
Each `demo` run first replaces the existing `demo` queue state. The configured
`runqueues` worker waits for that batch, dispatches entries sequentially, and
lets their handlers complete concurrently. Use the dashboard Refresh button
after starting a new batch.

To run the optional containerized dashboard instead:

```sh
docker compose --profile dashboard up -d
```
