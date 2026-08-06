import pytest
from django.core.exceptions import ImproperlyConfigured

import django_queue
from django_queue.apps import DjangoQueueConfig
from django_queue.backends import InvalidQueueBackendError, MemoryQueue
from django_queue.backends.exceptions import QueueException


class AttributeErrorBackend:
    def __init__(self, location, options):
        raise AttributeError("invalid location")


class ValueErrorBackend:
    def __init__(self, location, options):
        raise ValueError("invalid option")


class TestConfiguredQueueInitialization:
    def test_invalid_backend_errors_are_queue_and_django_configuration_errors(self):
        assert issubclass(QueueException, Exception)
        assert issubclass(InvalidQueueBackendError, QueueException)
        assert issubclass(InvalidQueueBackendError, ImproperlyConfigured)

    def test_initialises_each_configured_queue_and_reuses_it_on_repeat(self):
        handler = django_queue.QueueHandler(
            {
                "default": {
                    "BACKEND": "django_queue.backends.MemoryQueue",
                    "LOCATION": "",
                    "maxsize": 4,
                },
                "events": {
                    "BACKEND": "django_queue.backends.MemoryQueue",
                    "LOCATION": "",
                },
            }
        )

        django_queue.initialise_queues(handler)
        default = handler["default"]
        events = handler["events"]
        django_queue.initialise_queues(handler)

        assert isinstance(default, MemoryQueue)
        assert default.capacity == 4
        assert default is handler["default"]
        assert events is handler["events"]

    @pytest.mark.parametrize(
        ("settings", "message"),
        [
            ("not a configuration mapping", "QUEUES must be a mapping"),
            ({0: {"BACKEND": "django_queue.backends.MemoryQueue"}}, "alias 0"),
            ({"": {"BACKEND": "django_queue.backends.MemoryQueue"}}, "alias ''"),
            ({"requests": {}}, "requests.*BACKEND"),
            ({"requests": {"BACKEND": ""}}, "requests.*BACKEND"),
            ({"requests": {"BACKEND": 42}}, "requests.*BACKEND"),
            ({"requests": "not a configuration"}, "requests.*mapping"),
        ],
        ids=[
            "non-mapping-root",
            "non-string-alias",
            "empty-alias",
            "missing-backend",
            "empty-backend",
            "non-string-backend",
            "non-mapping-options",
        ],
    )
    def test_rejects_invalid_queue_configuration(self, settings, message):
        handler = django_queue.QueueHandler(settings)

        with pytest.raises(InvalidQueueBackendError, match=message):
            django_queue.initialise_queues(handler)

    def test_invalid_settings_message_uses_the_handler_settings_name(self):
        class CustomQueueHandler(django_queue.QueueHandler):
            settings_name = "CUSTOM_QUEUES"

        handler = CustomQueueHandler("not a configuration mapping")

        with pytest.raises(InvalidQueueBackendError, match="CUSTOM_QUEUES must be a mapping"):
            django_queue.initialise_queues(handler)

    @pytest.mark.parametrize(
        ("backend", "message"),
        [
            ("tests.test_configured_queues.AttributeErrorBackend", "default.*invalid location"),
            ("tests.test_configured_queues.ValueErrorBackend", "default.*invalid option"),
        ],
        ids=["attribute-error", "value-error"],
    )
    def test_wraps_backend_configuration_errors_with_the_queue_alias(self, backend, message):
        handler = django_queue.QueueHandler({"default": {"BACKEND": backend}})

        with pytest.raises(InvalidQueueBackendError, match=message):
            django_queue.initialise_queues(handler)

    def test_app_ready_initializes_the_configured_registry(self, monkeypatch):
        handler = django_queue.QueueHandler(
            {
                "default": {
                    "BACKEND": "django_queue.backends.MemoryQueue",
                    "LOCATION": "",
                }
            }
        )
        monkeypatch.setattr(django_queue, "queues", handler)
        config = DjangoQueueConfig("django_queue", django_queue)

        config.ready()

        assert isinstance(handler["default"], MemoryQueue)
