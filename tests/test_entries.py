import json
from dataclasses import FrozenInstanceError, dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from django_queue.entries import QueueEntry, QueueEntryStatus, validate_json_value
from tests.helpers import FIXED_UUID7, CustomQueueEntry


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
                    }
                ),
            ),
            (QueueEntryStatus.SUCCEEDED, frozenset()),
            (QueueEntryStatus.FAILED, frozenset()),
            (QueueEntryStatus.CANCELLED, frozenset()),
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
            "queued_at": entry.queued_at.isoformat(),
            "dispatched_at": None,
            "finished_at": None,
            "payload": {"request_id": 42},
            "result": None,
            "error": None,
        }

    def test_round_trips_a_terminal_entry(self):
        entry = QueueEntry(
            id=FIXED_UUID7,
            queue="requests",
            status=QueueEntryStatus.SUCCEEDED,
            queued_at=datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
            dispatched_at=datetime(2026, 8, 6, 12, 1, tzinfo=UTC),
            finished_at=datetime(2026, 8, 6, 12, 2, tzinfo=UTC),
            payload=["source", 3],
            result={"accepted": True},
            error=None,
        )

        restored = QueueEntry.from_dict(entry.to_dict())

        assert restored == entry

    def test_round_trips_a_field_declared_by_a_subclass(self):
        entry = CustomQueueEntry(
            id=FIXED_UUID7,
            queue="requests",
            status=QueueEntryStatus.SUCCEEDED,
            queued_at=datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
            dispatched_at=datetime(2026, 8, 6, 12, 1, tzinfo=UTC),
            finished_at=datetime(2026, 8, 6, 12, 2, tzinfo=UTC),
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
                queued_at=datetime(2026, 8, 6, 12, 0, tzinfo=UTC),
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
