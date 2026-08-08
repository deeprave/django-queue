import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from django_queue.clock import MICROSECONDS_PER_SECOND, ClockTime

# 2026-08-03 23:33:20.250000 UTC, expressed three ways.
SECONDS = 1_785_800_000
MICROSECONDS = 250_000
TIMESTAMP = 1_785_800_000.25
MOMENT = datetime(2026, 8, 3, 23, 33, 20, MICROSECONDS, tzinfo=UTC)


class TestConstruction:
    def test_the_same_moment_from_each_source_is_equal(self):
        from_pair = ClockTime.from_timeval(SECONDS, MICROSECONDS)
        from_float = ClockTime.from_timestamp(TIMESTAMP)
        from_aware = ClockTime.from_datetime(MOMENT)

        assert from_pair == from_float == from_aware

    def test_holds_whole_seconds_and_microseconds(self):
        instant = ClockTime.from_timestamp(TIMESTAMP)

        assert instant.seconds == SECONDS
        assert instant.microseconds == MICROSECONDS

    def test_is_immutable(self):
        instant = ClockTime.from_timeval(SECONDS, MICROSECONDS)

        with pytest.raises(AttributeError):
            instant.seconds = 0

    @pytest.mark.parametrize("microseconds", [1_000_000, 1_000_001, -1])
    def test_rejects_a_microsecond_component_out_of_range(self, microseconds):
        with pytest.raises(ValueError, match="microseconds"):
            ClockTime(SECONDS, microseconds)

    @pytest.mark.parametrize(
        ("seconds", "microseconds"),
        [(1.5, 0), (SECONDS, 0.5)],
    )
    def test_rejects_a_component_that_is_not_a_whole_number(
        self, seconds, microseconds
    ):
        with pytest.raises(TypeError, match="whole number"):
            ClockTime(seconds, microseconds)

    @pytest.mark.parametrize(
        ("seconds", "microseconds"),
        [(True, 0), (SECONDS, False), (True, True)],
    )
    def test_rejects_a_boolean_component(self, seconds, microseconds):
        """bool is an int, but a flag does not describe a moment."""
        with pytest.raises(TypeError, match="whole number"):
            ClockTime(seconds, microseconds)

    def test_rejects_a_boolean_timestamp(self):
        with pytest.raises(TypeError, match="whole number"):
            ClockTime.from_timestamp(True)

    @pytest.mark.parametrize("timestamp", [float("nan"), float("inf"), float("-inf")])
    def test_rejects_a_timestamp_that_is_not_finite(self, timestamp):
        with pytest.raises(ValueError, match="finite"):
            ClockTime.from_timestamp(timestamp)

    def test_rejects_a_negative_second_component(self):
        with pytest.raises(ValueError, match="before the epoch"):
            ClockTime(-1, 0)

    def test_rejects_a_negative_timestamp(self):
        with pytest.raises(ValueError, match="before the epoch"):
            ClockTime.from_timestamp(-0.5)

    def test_rejects_a_datetime_before_the_epoch(self):
        with pytest.raises(ValueError, match="before the epoch"):
            ClockTime.from_datetime(datetime(1969, 7, 20, tzinfo=UTC))

    def test_rejects_a_datetime_without_a_zone(self):
        with pytest.raises(ValueError, match="aware"):
            ClockTime.from_datetime(MOMENT.replace(tzinfo=None))

    def test_accepts_the_epoch_itself(self):
        assert ClockTime.from_timestamp(0.0) == ClockTime(0, 0)

    def test_reads_a_non_utc_datetime_as_the_moment_it_describes(self):
        elsewhere = MOMENT.astimezone(timezone(timedelta(hours=10)))

        assert ClockTime.from_datetime(elsewhere) == ClockTime.from_datetime(MOMENT)


class TestConversion:
    def test_converts_to_a_count_of_seconds(self):
        assert ClockTime.from_timeval(SECONDS, MICROSECONDS).to_timestamp() == TIMESTAMP

    @pytest.mark.parametrize("microseconds", range(0, MICROSECONDS_PER_SECOND, 997))
    def test_round_trips_through_a_count_of_seconds(self, microseconds):
        instant = ClockTime.from_timeval(SECONDS, microseconds)

        assert ClockTime.from_timestamp(instant.to_timestamp()) == instant

    def test_converts_to_an_aware_utc_datetime(self):
        moment = ClockTime.from_timeval(SECONDS, MICROSECONDS).to_datetime()

        assert moment == MOMENT
        assert moment.tzinfo is UTC

    def test_round_trips_through_a_datetime(self):
        instant = ClockTime.from_timeval(SECONDS, MICROSECONDS)

        assert ClockTime.from_datetime(instant.to_datetime()) == instant

    @pytest.mark.parametrize("seconds", [SECONDS, 2**33, 2**34])
    def test_round_trips_through_a_datetime_at_any_magnitude(self, seconds):
        """A datetime carries exact microseconds, so no precision is lost."""
        instant = ClockTime.from_timeval(seconds, 999_999)

        assert ClockTime.from_datetime(instant.to_datetime()) == instant

    def test_does_not_coerce_to_a_number(self):
        with pytest.raises(TypeError):
            float(ClockTime.from_timeval(SECONDS, MICROSECONDS))

    def test_is_not_serialised_as_a_number_by_accident(self):
        with pytest.raises(TypeError):
            json.dumps({"queued_at": ClockTime.from_timeval(SECONDS, MICROSECONDS)})


class TestOrdering:
    def test_orders_within_the_same_second_by_microsecond(self):
        earlier = ClockTime.from_timeval(SECONDS, 1)
        later = ClockTime.from_timeval(SECONDS, 2)

        assert earlier < later
        assert later > earlier

    def test_orders_across_seconds_ahead_of_any_microsecond(self):
        end_of_second = ClockTime.from_timeval(SECONDS, MICROSECONDS_PER_SECOND - 1)
        next_second = ClockTime.from_timeval(SECONDS + 1, 0)

        assert end_of_second < next_second

    def test_sorts_chronologically(self):
        instants = [
            ClockTime.from_timeval(SECONDS + 1, 0),
            ClockTime.from_timeval(SECONDS, 999_999),
            ClockTime.from_timeval(SECONDS, 1),
        ]

        assert sorted(instants) == [instants[2], instants[1], instants[0]]


class TestArithmetic:
    def test_subtracting_instants_yields_the_seconds_between_them(self):
        started = ClockTime.from_timeval(SECONDS, 250_000)
        finished = ClockTime.from_timeval(SECONDS + 2, 750_000)

        assert finished - started == pytest.approx(2.5)

    def test_elapsed_time_is_negative_when_the_order_is_reversed(self):
        started = ClockTime.from_timeval(SECONDS, 250_000)
        finished = ClockTime.from_timeval(SECONDS + 2, 750_000)

        assert started - finished == pytest.approx(-2.5)

    def test_adding_a_duration_yields_a_later_instant(self):
        started = ClockTime.from_timeval(SECONDS, 250_000)

        assert started + 2.5 == ClockTime.from_timeval(SECONDS + 2, 750_000)

    def test_subtracting_a_duration_yields_an_earlier_instant(self):
        finished = ClockTime.from_timeval(SECONDS + 2, 750_000)

        assert finished - 2.5 == ClockTime.from_timeval(SECONDS, 250_000)

    def test_a_duration_may_lead_the_instant(self):
        started = ClockTime.from_timeval(SECONDS, 250_000)

        assert 2.5 + started == started + 2.5

    @pytest.mark.parametrize("duration", [float("nan"), float("inf"), float("-inf")])
    def test_shifting_by_a_duration_that_is_not_finite_is_rejected(self, duration):
        with pytest.raises(ValueError, match="finite"):
            ClockTime.from_timeval(SECONDS, 0) + duration

    def test_a_shift_landing_before_the_epoch_is_rejected(self):
        with pytest.raises(ValueError, match="before the epoch"):
            ClockTime(1, 0) - 2.0

    def test_adding_two_instants_is_unsupported(self):
        instant = ClockTime.from_timeval(SECONDS, 0)

        with pytest.raises(TypeError):
            instant + instant
