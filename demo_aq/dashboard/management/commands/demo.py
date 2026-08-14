"""Publish a random batch of scheduled manual-page entries."""

from __future__ import annotations

import asyncio
import random
import subprocess
from collections.abc import Sequence

from django.core.management.base import BaseCommand, CommandError

from dashboard.demo_worker import build_demo_payload
from django_queue import queues


class Command(BaseCommand):
    help = "Clear and publish a random batch of demo entries."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--min",
            type=int,
            default=6,
            help="Minimum number of manual-page entries to publish (default: 6).",
        )
        parser.add_argument(
            "--max",
            type=int,
            default=16,
            help="Maximum number of manual-page entries to publish (default: 16).",
        )

    def handle(self, *args, **options) -> None:
        min_entries = options["min"]
        max_entries = options["max"]
        if min_entries < 1:
            raise CommandError("--min must be at least 1")
        if max_entries < min_entries:
            raise CommandError("--max must be at least --min")

        messages = _random_messages(min_entries, max_entries)
        asyncio.run(self._run(messages))

    async def _run(self, messages: Sequence[str]) -> None:
        queue = queues["demo"]
        await _clear_demo_queue(queue)
        failure_count = _failure_count(len(messages))
        failing_indices = set(random.sample(range(len(messages)), failure_count))
        payloads = [
            build_demo_payload(message, index in failing_indices)
            for index, message in enumerate(messages)
        ]

        await asyncio.gather(*(queue.aenqueue(payload) for payload in payloads))
        self.stdout.write(f"Published {len(payloads)} entries to the demo queue.")


def _random_messages(min_entries: int, max_entries: int) -> list[str]:
    """Return a random-sized batch selected from the manual-page keyword index."""
    try:
        result = subprocess.run(
            ["man", "-k", "."],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise CommandError("'man' is required to generate demo entries") from exc
    except subprocess.CalledProcessError as exc:
        raise CommandError("'man -k .' could not generate demo entries") from exc

    messages = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not messages:
        raise CommandError("'man -k .' did not return any demo entries")

    count = random.randint(min_entries, max_entries)
    return random.choices(messages, k=count)


def _failure_count(entry_count: int) -> int:
    """Return one failure for small batches and two for larger demo batches."""
    return 1 if entry_count < 10 else 2


async def _clear_demo_queue(queue) -> None:
    """Remove this demo queue's retained task state before publishing a batch."""
    client = queue._async_redis()
    keys = [queue.queue_name]
    async for key in client.scan_iter(match=f"{queue.queue_name}:entries:*"):
        keys.append(key)
    await client.delete(*keys)
