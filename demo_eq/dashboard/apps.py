from django.apps import AppConfig


class DashboardConfig(AppConfig):
    name = "dashboard"

    def ready(self) -> None:
        from . import listeners  # noqa: F401 - registers the demo event listener.
