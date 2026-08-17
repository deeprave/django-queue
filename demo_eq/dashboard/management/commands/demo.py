"""Publish a continuous movement stream to the event queue demo."""

from __future__ import annotations

import asyncio

from django.core.management.base import BaseCommand, CommandError

from django_queue import queues
from django_queue.backends.base import EventQueue

from ...movement import MovementGenerator


class Command(BaseCommand):
    """Publish one plausible JSON movement event per second."""

    help = "Publish one movement event per second to the demo event queue."

    def handle(self, *args, **options) -> None:
        queue = queues["movement"]
        if not isinstance(queue, EventQueue):
            raise CommandError(
                "The movement queue must be configured as an EventQueue."
            )
        try:
            asyncio.run(self._publish(queue))
        except KeyboardInterrupt:
            self.stdout.write("Stopped movement event producer.")

    async def _publish(self, queue: EventQueue) -> None:
        generator = MovementGenerator()
        loop = asyncio.get_running_loop()
        next_event_at = loop.time()
        while True:
            payload = generator.next()
            await queue.aenqueue(payload)
            self.stdout.write(str(payload))
            next_event_at += 1
            await asyncio.sleep(max(0, next_event_at - loop.time()))
