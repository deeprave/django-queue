import pytest
from django.core.exceptions import ImproperlyConfigured

import django_queue
from django_queue.apps import DjangoQueueConfig
from django_queue.backends import InvalidQueueBackendError, MemoryQueue
from django_queue.backends.exceptions import QueueException
from django_queue.entries import QueueEntry
from django_queue.worker import AsyncQueueWorker


class AttributeErrorBackend:
    def __init__(self, location, options):
        raise AttributeError("invalid location")


class ValueErrorBackend:
    def __init__(self, location, options):
        raise ValueError("invalid option")


class HandlerMetadataBackend(MemoryQueue):
    def __init__(self, location, options):
        assert "HANDLER" not in options
        assert "WORKER" not in options
        assert "ENTRY_CLASS" not in options
        super().__init__(location, options)


class TrackingWorker(AsyncQueueWorker):
    instances = 0

    def __init__(self, *args, **kwargs):
        type(self).instances += 1
        super().__init__(*args, **kwargs)


class TrackingEntry(QueueEntry):
    instances = 0

    def __post_init__(self):
        type(self).instances += 1
        super().__post_init__()


async def no_op_handler(entry):
    return None


@pytest.fixture(autouse=True)
def reset_tracking_extension_instances():
    TrackingWorker.instances = 0
    TrackingEntry.instances = 0
    yield
    TrackingWorker.instances = 0
    TrackingEntry.instances = 0


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

        with pytest.raises(
            InvalidQueueBackendError, match="CUSTOM_QUEUES must be a mapping"
        ):
            django_queue.initialise_queues(handler)

    @pytest.mark.parametrize(
        ("backend", "message"),
        [
            (
                "tests.test_configured_queues.AttributeErrorBackend",
                "default.*invalid location",
            ),
            (
                "tests.test_configured_queues.ValueErrorBackend",
                "default.*invalid option",
            ),
        ],
        ids=["attribute-error", "value-error"],
    )
    def test_wraps_backend_configuration_errors_with_the_queue_alias(
        self, backend, message
    ):
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

    def test_preserves_handler_metadata_without_passing_it_to_the_backend(self):
        handler = django_queue.QueueHandler(
            {
                "default": {
                    "BACKEND": "tests.test_configured_queues.HandlerMetadataBackend",
                    "HANDLER": "tests.test_runqueues.handle_entry",
                    "LOCATION": "",
                }
            }
        )

        django_queue.initialise_queues(handler)

        assert isinstance(handler["default"], HandlerMetadataBackend)
        assert (
            handler.settings["default"]["HANDLER"]
            == "tests.test_runqueues.handle_entry"
        )

    @pytest.mark.parametrize(
        ("worker", "entry_class"),
        [
            (TrackingWorker, TrackingEntry),
            (
                "tests.test_configured_queues.TrackingWorker",
                "tests.test_configured_queues.TrackingEntry",
            ),
        ],
        ids=["class-objects", "dotted-paths"],
    )
    def test_preserves_worker_extension_until_queue_activation(
        self, worker, entry_class
    ):
        handler = django_queue.QueueHandler(
            {
                "default": {
                    "BACKEND": "tests.test_configured_queues.HandlerMetadataBackend",
                    "WORKER": worker,
                    "ENTRY_CLASS": entry_class,
                    "LOCATION": "",
                }
            }
        )

        django_queue.initialise_queues(handler)

        assert handler.settings["default"]["WORKER"] is worker
        assert handler.settings["default"]["ENTRY_CLASS"] is entry_class
        assert handler["default"].worker_class is worker
        handler["default"].create_worker("default", no_op_handler)
        assert TrackingWorker.instances == 1
        assert handler["default"].entry_class is TrackingEntry
        assert TrackingEntry.instances == 0

    @pytest.mark.parametrize(
        ("setting", "value", "message"),
        [
            ("WORKER", "tests.test_configured_queues.TrackingEntry", "WORKER"),
            (
                "ENTRY_CLASS",
                "tests.test_configured_queues.TrackingWorker",
                "ENTRY_CLASS",
            ),
            ("WORKER", "tests.test_configured_queues.UnknownWorker", "WORKER"),
            ("ENTRY_CLASS", "tests.test_configured_queues.UnknownEntry", "ENTRY_CLASS"),
        ],
        ids=[
            "entry-as-worker",
            "worker-as-entry",
            "missing-worker-path",
            "missing-entry-path",
        ],
    )
    def test_rejects_invalid_queue_type_extensions(self, setting, value, message):
        handler = django_queue.QueueHandler(
            {
                "default": {
                    "BACKEND": "django_queue.backends.MemoryQueue",
                    setting: value,
                }
            }
        )

        if setting == "WORKER":
            django_queue.initialise_queues(handler)
            with pytest.raises(InvalidQueueBackendError, match=f"default.*{message}"):
                handler["default"].create_worker("default", no_op_handler)
        else:
            with pytest.raises(InvalidQueueBackendError, match=f"default.*{message}"):
                django_queue.initialise_queues(handler)
