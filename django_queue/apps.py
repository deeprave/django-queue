from django.apps import AppConfig


class DjangoQueueConfig(AppConfig):
    name = "django_queue"

    def ready(self) -> None:
        from django_queue import initialise_queues

        initialise_queues()
