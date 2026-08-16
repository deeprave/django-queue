"""Publish a random batch of generated demo entries."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Sequence

from django.core.management.base import BaseCommand, CommandError

from dashboard.demo_worker import build_demo_payload, generate_demo_message
from django_queue import queues


class Command(BaseCommand):
    help = "Clear and publish a random batch of demo entries."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--min",
            type=int,
            default=6,
            help="Minimum number of generated entries to publish (default: 6).",
        )
        parser.add_argument(
            "--max",
            type=int,
            default=16,
            help="Maximum number of generated entries to publish (default: 16).",
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
        payloads = [
            build_demo_payload(message, should_fail=random.randrange(8) == 0)
            for message in messages
        ]

        await asyncio.gather(*(queue.aenqueue(payload) for payload in payloads))
        self.stdout.write(f"Published {len(payloads)} entries to the demo queue.")


def _random_messages(min_entries: int, max_entries: int) -> list[str]:
    count = random.randint(min_entries, max_entries)
    return [generate_demo_message() for _ in range(count)]


async def _clear_demo_queue(queue) -> None:
    """Remove this demo queue's retained task state before publishing a batch."""
    # Deliberately provider-local demo-fixture cleanup; AsyncQueue does not
    # expose a general reset API because normal entries may only be pruned once
    # terminal.
    await queue._provider.aclear_entries()
