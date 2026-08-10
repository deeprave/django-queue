import json
from dataclasses import FrozenInstanceError, dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from django_queue.clock import ClockTime
from django_queue.entries import QueueEntry, QueueEntryStatus, validate_json_value
from tests.helpers import FIXED_CLOCK_TIME, FIXED_UUID7, CustomQueueEntry


class TestQueueEntry:
    @pytest.mark.parametrize(
        ("status", "next_states"),
        [
            (QueueEntryStatus.QUEUED, frozenset({QueueEntryStatus.RUNNING})),
            (
                QueueEntryStatus.RUNNING,
                frozenset(
                    {
                        QueueEntryStatus.SUCCEEDED,
                        QueueEntryStatus.FAILED,
                        QueueEntryStatus.CANCELLED,
                        QueueEntryStatus.TIMEOUT,
                    }
                ),
            ),
            (QueueEntryStatus.SUCCEEDED, frozenset()),
            (QueueEntryStatus.FAILED, frozenset()),
            (QueueEntryStatus.CANCELLED, frozenset()),
            (QueueEntryStatus.TIMEOUT, frozenset()),
        ],
    )
    def test_lists_valid_next_states(self, status, next_states):
        assert status.next_state() == next_states

    def test_serialises_a_queued_entry_as_a_complete_json_record(self):
        entry = QueueEntry.create(queue="requests", payload={"request_id": 42})

        stored = entry.to_dict()

        assert UUID(stored["id"]).version == 7
        assert stored == {
            "id": str(entry.id),
            "queue": "requests",
            "status": "queued",
            "queued_at": entry.queued_at.to_timestamp(),
            "dispatched_at": None,
            "finished_at": None,
            "payload": {"request_id": 42},
            "result": None,
            "error": None,
            "timeout_seconds": None,
        }

    def test_round_trips_a_terminal_entry(self):
        entry = QueueEntry(
            id=FIXED_UUID7,
            queue="requests",
            status=QueueEntryStatus.SUCCEEDED,
            queued_at=FIXED_CLOCK_TIME,
            dispatched_at=FIXED_CLOCK_TIME + 60,
            finished_at=FIXED_CLOCK_TIME + 120,
            payload=["source", 3],
            result={"accepted": True},
            error=None,
        )

        restored = QueueEntry.from_dict(entry.to_dict())

        assert restored == entry

    @pytest.mark.parametrize(
        "queued_at",
        [datetime(2026, 8, 6, 12, 0, tzinfo=UTC), 1_786_032_000.0, None],
    )
    def test_rejects_a_lifecycle_timestamp_that_is_not_an_instant(self, queued_at):
        """Otherwise the failure surfaces at to_dict, far from its cause."""
        with pytest.raises(TypeError, match="ClockTime"):
            QueueEntry(
                id=FIXED_UUID7,
                queue="requests",
                status=QueueEntryStatus.QUEUED,
                queued_at=queued_at,
                dispatched_at=None,
                finished_at=None,
                payload=None,
                result=None,
                error=None,
            )

    @pytest.mark.parametrize("field", ["dispatched_at", "finished_at"])
    def test_rejects_an_optional_timestamp_that_is_not_an_instant(self, field):
        with pytest.raises(TypeError, match="ClockTime"):
            QueueEntry(
                id=FIXED_UUID7,
                queue="requests",
                status=QueueEntryStatus.QUEUED,
                queued_at=FIXED_CLOCK_TIME,
                payload=None,
                result=None,
                error=None,
                **{
                    "dispatched_at": None,
                    "finished_at": None,
                    field: datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
                },
            )

    @pytest.mark.parametrize(
        ("field", "value", "match"),
        [("id", "not-a-uuid", "UUID"), ("queue", 42, "string")],
    )
    def test_rejects_an_identifier_or_queue_name_of_the_wrong_type(
        self, field, value, match
    ):
        with pytest.raises(TypeError, match=match):
            QueueEntry(
                **{
                    "id": FIXED_UUID7,
                    "queue": "requests",
                    "status": QueueEntryStatus.QUEUED,
                    "queued_at": FIXED_CLOCK_TIME,
                    "dispatched_at": None,
                    "finished_at": None,
                    "payload": None,
                    "result": None,
                    "error": None,
                    field: value,
                }
            )

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("id", "not-a-uuid"),
            ("status", "bogus"),
            ("queue", 42),
            ("queued_at", "not-a-number"),
            ("queued_at", -1.0),
            ("dispatched_at", "not-a-number"),
            ("dispatched_at", -1.0),
            ("finished_at", "not-a-number"),
        ],
    )
    def test_rejects_a_malformed_durable_record_naming_the_field(self, field, value):
        """One exception class whichever way the stored value is wrong."""
        stored = QueueEntry.create(queue="requests", payload=None).to_dict()

        with pytest.raises(ValueError, match=rf"Queue entry .*\b{field}\b") as raised:
            QueueEntry.from_dict(stored | {field: value})

        assert isinstance(raised.value.__cause__, TypeError | ValueError)

    def test_rejects_a_restored_record_with_an_unrecognised_status(self):
        stored = QueueEntry.create(queue="requests", payload=None).to_dict()

        with pytest.raises(ValueError, match=r"Queue entry .*\bstatus\b"):
            QueueEntry.from_dict(stored | {"status": "expired"})

    @pytest.mark.parametrize("budget", ["2.5", True, object()])
    def test_rejects_an_execution_budget_that_is_not_a_number(self, budget):
        """A budget is a count of seconds, like every other duration here."""
        with pytest.raises(TypeError, match="timeout_seconds"):
            QueueEntry(
                id=FIXED_UUID7,
                queue="requests",
                status=QueueEntryStatus.QUEUED,
                queued_at=FIXED_CLOCK_TIME,
                dispatched_at=None,
                finished_at=None,
                payload=None,
                result=None,
                error=None,
                timeout_seconds=budget,
            )

    def test_carries_no_budget_when_enqueued_without_one(self):
        entry = QueueEntry.create(queue="requests", payload=None)

        assert entry.timeout_seconds is None
        assert entry.to_dict()["timeout_seconds"] is None

    def test_round_trips_an_execution_budget(self):
        entry = QueueEntry(
            id=FIXED_UUID7,
            queue="requests",
            status=QueueEntryStatus.QUEUED,
            queued_at=FIXED_CLOCK_TIME,
            dispatched_at=None,
            finished_at=None,
            payload=None,
            result=None,
            error=None,
            timeout_seconds=2.5,
        )

        stored = entry.to_dict()
        restored = QueueEntry.from_dict(json.loads(json.dumps(stored)))

        assert stored["timeout_seconds"] == 2.5
        assert restored == entry

    def test_rejects_a_restored_record_that_omits_a_required_field(self):
        """A missing key must not surface the dataclass constructor's own error."""
        stored = QueueEntry.create(queue="requests", payload=None).to_dict()

        with pytest.raises(ValueError, match=r"Queue entry .*\bqueued_at\b"):
            QueueEntry.from_dict(
                {name: v for name, v in stored.items() if name != "queued_at"}
            )

    def test_rejects_a_restored_record_whose_value_the_record_rejects(self):
        """A null where an instant belongs reads like any other bad record."""
        stored = QueueEntry.create(queue="requests", payload=None).to_dict()

        with pytest.raises(ValueError, match=r"Queue entry .*\bqueued_at\b") as raised:
            QueueEntry.from_dict(stored | {"queued_at": None})

        assert isinstance(raised.value.__cause__, TypeError)

    def test_reports_how_long_it_waited_and_ran(self):
        entry = QueueEntry(
            id=FIXED_UUID7,
            queue="requests",
            status=QueueEntryStatus.SUCCEEDED,
            queued_at=FIXED_CLOCK_TIME,
            dispatched_at=FIXED_CLOCK_TIME + 2.5,
            finished_at=FIXED_CLOCK_TIME + 9,
            payload=None,
            result=None,
            error=None,
        )

        assert entry.queued_for == pytest.approx(2.5)
        assert entry.ran_for == pytest.approx(6.5)

    @pytest.mark.parametrize(
        ("microseconds", "expected"),
        [(1, 1e-06), (137, 0.000137), (2_500, 0.0025), (999_999, 0.999999)],
    )
    def test_reports_a_sub_second_duration_without_truncating(
        self, microseconds, expected
    ):
        """Most handlers finish in well under a second; whole seconds would be 0."""
        entry = QueueEntry(
            id=FIXED_UUID7,
            queue="requests",
            status=QueueEntryStatus.SUCCEEDED,
            queued_at=FIXED_CLOCK_TIME,
            dispatched_at=FIXED_CLOCK_TIME,
            finished_at=ClockTime(
                FIXED_CLOCK_TIME.seconds, FIXED_CLOCK_TIME.microseconds + microseconds
            ),
            payload=None,
            result=None,
            error=None,
        )

        assert entry.ran_for == pytest.approx(expected)

    def test_reports_no_duration_before_the_instants_exist(self):
        queued = QueueEntry.create(queue="requests", payload=None)
        running = replace(queued, dispatched_at=queued.queued_at + 1)

        assert queued.queued_for is None
        assert queued.ran_for is None
        assert running.queued_for == pytest.approx(1)
        assert running.ran_for is None

    def test_reports_no_duration_when_the_instants_contradict(self):
        """A clock that moved backwards cannot describe an elapsed time."""
        entry = QueueEntry(
            id=FIXED_UUID7,
            queue="requests",
            status=QueueEntryStatus.SUCCEEDED,
            queued_at=FIXED_CLOCK_TIME + 50,
            dispatched_at=FIXED_CLOCK_TIME,
            finished_at=FIXED_CLOCK_TIME - 10,
            payload=None,
            result=None,
            error=None,
        )

        assert entry.queued_for is None
        assert entry.ran_for is None

    def test_keeps_durations_out_of_the_durable_record(self):
        entry = QueueEntry(
            id=FIXED_UUID7,
            queue="requests",
            status=QueueEntryStatus.SUCCEEDED,
            queued_at=FIXED_CLOCK_TIME,
            dispatched_at=FIXED_CLOCK_TIME + 2.5,
            finished_at=FIXED_CLOCK_TIME + 9,
            payload=None,
            result=None,
            error=None,
        )

        stored = entry.to_dict()
        restored = QueueEntry.from_dict(json.loads(json.dumps(stored)))

        assert "queued_for" not in stored
        assert "ran_for" not in stored
        assert restored.queued_for == entry.queued_for
        assert restored.ran_for == entry.ran_for

    def test_holds_lifecycle_timestamps_as_instants(self):
        entry = QueueEntry.create(queue="requests", payload=None)

        assert isinstance(entry.queued_at, ClockTime)

    def test_stores_lifecycle_timestamps_as_a_count_of_seconds(self):
        entry = QueueEntry.create(
            queue="requests", payload=None, queued_at=ClockTime(1_786_032_000, 250_000)
        )

        assert entry.to_dict()["queued_at"] == 1_786_032_000.25

    def test_restores_lifecycle_timestamps_to_an_equal_instant(self):
        entry = QueueEntry.create(
            queue="requests", payload=None, queued_at=ClockTime(1_786_032_000, 999_999)
        )

        restored = QueueEntry.from_dict(json.loads(json.dumps(entry.to_dict())))

        assert restored.queued_at == entry.queued_at

    def test_stores_no_timezone_and_needs_no_parsing(self):
        entry = QueueEntry.create(queue="requests", payload=None)

        assert isinstance(entry.to_dict()["queued_at"], float)

    def test_round_trips_a_field_declared_by_a_subclass(self):
        entry = CustomQueueEntry(
            id=FIXED_UUID7,
            queue="requests",
            status=QueueEntryStatus.SUCCEEDED,
            queued_at=FIXED_CLOCK_TIME,
            dispatched_at=FIXED_CLOCK_TIME + 60,
            finished_at=FIXED_CLOCK_TIME + 120,
            payload=["source", 3],
            result={"accepted": True},
            error=None,
            kind="reconciliation",
        )

        stored = entry.to_dict()
        restored = CustomQueueEntry.from_dict(json.loads(json.dumps(stored)))

        # Asserted against a non-default value: a dropped field would otherwise
        # be masked by the subclass default on restore.
        assert stored["kind"] == "reconciliation"
        assert restored == entry

    def test_rejects_a_non_json_field_declared_by_a_subclass(self):
        @dataclass(frozen=True, slots=True)
        class ScheduledEntry(QueueEntry):
            scheduled_at: datetime | None = None

        with pytest.raises(TypeError, match="JSON-serialisable"):
            ScheduledEntry(
                id=FIXED_UUID7,
                queue="requests",
                status=QueueEntryStatus.QUEUED,
                queued_at=FIXED_CLOCK_TIME,
                dispatched_at=None,
                finished_at=None,
                payload=None,
                result=None,
                error=None,
                scheduled_at=datetime(2026, 8, 7, tzinfo=UTC),
            )

    def test_restores_a_subclass_field_absent_from_the_record(self):
        entry = CustomQueueEntry.create(queue="requests", payload=None)
        stored = entry.to_dict()
        del stored["kind"]

        assert CustomQueueEntry.from_dict(stored).kind == "task"

    def test_is_immutable(self):
        entry = QueueEntry.create(queue="requests", payload=None)

        with pytest.raises(FrozenInstanceError):
            entry.queue = "other"  # type: ignore[misc]

    def test_requires_a_status_enum_in_memory(self):
        entry = QueueEntry.create(queue="requests", payload=None)

        with pytest.raises(TypeError, match="QueueEntryStatus"):
            replace(entry, status="queued")


@pytest.mark.parametrize("payload", [{"nested": [1, None, False]}, "text", 1.2, None])
def test_validate_json_value_accepts_json_values(payload):
    validate_json_value(payload)


@pytest.mark.parametrize("payload", [{"invalid"}, {"key": object()}])
def test_validate_json_value_rejects_non_json_values(payload):
    with pytest.raises(TypeError, match="JSON-serialisable"):
        validate_json_value(payload)
