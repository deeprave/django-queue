from django.apps import AppConfig
from django.core.signals import request_started


def _start_event_runtime_on_request(**kwargs) -> None:
    """Start process-local event dispatch after this worker handles a request."""
    from django_queue import initialise_queues
    from django_queue.event_runtime import event_runtime

    event_runtime.start(initialise_queues())


class DjangoQueueConfig(AppConfig):
    name = "django_queue"

    def ready(self) -> None:
        from django_queue import initialise_queues

        initialise_queues()
        request_started.connect(
            _start_event_runtime_on_request,
            dispatch_uid="django_queue.start_event_runtime_on_request",
        )
