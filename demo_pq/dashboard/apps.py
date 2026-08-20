import sys

from django.apps import AppConfig


class DashboardConfig(AppConfig):
    name = "dashboard"

    def ready(self) -> None:
        # runqueues's _activate_worker waits for the queue to already have
        # a pending entry before it ever constructs the worker and calls
        # its run() -- but this demo's per-tier injectors live inside
        # run() itself, so a genuinely empty queue deadlocks forever: the
        # worker never starts because nothing is pending, and nothing
        # becomes pending because only the worker's own injectors would do
        # that. Seed one entry per tier here, once, only when the command
        # being run is actually `runqueues`, so a bare `manage.py
        # runqueues` (without first running `manage.py demo`) still works.
        if len(sys.argv) > 1 and sys.argv[1] == "runqueues":
            from .demo_worker import seed_one_entry_per_tier

            seed_one_entry_per_tier()
