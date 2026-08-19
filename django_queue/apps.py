from django.apps import AppConfig


class DjangoQueueConfig(AppConfig):
    name = "django_queue"

    def ready(self) -> None:
        from django_queue import initialise_queues
        from django_queue.queue_runtime import queue_runtime

        registry = initialise_queues()
        if registry.settings:
            queue_runtime.start_thread()
            queue_runtime.start(registry)
