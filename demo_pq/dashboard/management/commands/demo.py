"""Publish a random batch of generated demo entries across priority tiers."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Sequence

from django.core.management.base import BaseCommand, CommandError

from dashboard.demo_worker import (
    PRIORITY_TIERS,
    build_demo_payload,
    generate_demo_message,
)
from django_queue import queues


class Command(BaseCommand):
    help = "Clear and publish a random batch of demo entries across priority tiers."

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

        batch = _random_batch(min_entries, max_entries)
        asyncio.run(self._run(batch))

    async def _run(self, tiers: Sequence[str]) -> None:
        queue = queues["demo"]
        await _clear_demo_queue(queue)
        await asyncio.gather(
            *(
                queue.aenqueue(
                    build_demo_payload(
                        generate_demo_message(tier),
                        tier,
                        should_fail=random.randrange(8) == 0,
                    ),
                    priority=PRIORITY_TIERS[tier]["priority"],
                )
                for tier in tiers
            )
        )
        self.stdout.write(f"Published {len(tiers)} entries to the demo queue.")


def _random_batch(min_entries: int, max_entries: int) -> list[str]:
    count = random.randint(min_entries, max_entries)
    tiers = list(PRIORITY_TIERS)
    return [random.choice(tiers) for _ in range(count)]


async def _clear_demo_queue(queue) -> None:
    """Remove this demo queue's retained task state before publishing a batch."""
    # Deliberately provider-local demo-fixture cleanup; AsyncQueue does not
    # expose a general reset API because normal entries may only be pruned once
    # terminal.
    await queue._provider.aclear_records()
