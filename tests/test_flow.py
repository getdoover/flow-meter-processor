"""Core maths: conversions, counter resets, integration, day rollover, alerts."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from flow_meter_processor.flow import (
    InputType,
    Settings,
    State,
    analog_to_lps,
    expire_rate,
    no_flow_due,
    process_reading,
    rate_factor,
    roll_day,
    to_display,
    totaliser_factor,
)

BNE = ZoneInfo("Australia/Brisbane")


def ms(y, mo, d, h=0, mi=0, s=0):
    return int(datetime(y, mo, d, h, mi, s, tzinfo=BNE).timestamp() * 1000)


T0 = ms(2026, 11, 19, 10, 0)
MIN = 60_000


def test_unit_factors():
    assert rate_factor("L/s") == 1
    assert rate_factor("L/min") == pytest.approx(1 / 60)
    assert rate_factor("kL/min") == pytest.approx(1000 / 60)
    assert rate_factor("m³/h") == pytest.approx(1000 / 3600)
    assert rate_factor("ML/day") == pytest.approx(1e6 / 86400)
    assert totaliser_factor("kL") == 1000
    assert totaliser_factor("m³") == 1000
    assert to_display(None, 1000) is None
    assert to_display(2500, 1000) == 2.5


def test_pulse_counts_and_rate():
    s = Settings(input_type=InputType.PULSE, litres_per_pulse=10.0, zone=BNE)
    st = State()
    process_reading(st, s, 100, T0)
    # First reading is a baseline only: volumes start at zero, rate unknown.
    assert st.total_l == 0 and st.today_l == 0
    assert st.rate_lps is None and st.flowing is None
    assert st.yesterday_l is None

    process_reading(st, s, 160, T0 + MIN)
    assert st.total_l == 600
    assert st.rate_lps == pytest.approx(10.0)
    assert st.flowing is True
    assert st.last_flow_ts == T0 + MIN

    process_reading(st, s, 160, T0 + 2 * MIN)
    assert st.rate_lps == 0
    assert st.flowing is False
    assert st.last_flow_ts == T0 + MIN


def test_counter_reset_never_negative():
    s = Settings(input_type=InputType.PULSE, litres_per_pulse=1.0, zone=BNE)
    st = State()
    process_reading(st, s, 1000, T0)
    process_reading(st, s, 1100, T0 + MIN)
    r = process_reading(st, s, 5, T0 + 2 * MIN)
    assert r.reset
    assert st.total_l == 100
    assert st.flowing is None
    # Counts on from the new baseline.
    process_reading(st, s, 25, T0 + 3 * MIN)
    assert st.total_l == 120


def test_totaliser_kl_with_multiplier():
    s = Settings(
        input_type=InputType.TOTALISER,
        totaliser_litres=totaliser_factor("kL") * 1.0,
        zone=BNE,
    )
    st = State()
    process_reading(st, s, 1234.5, T0)
    process_reading(st, s, 1236.0, T0 + 10 * MIN)
    assert st.total_l == pytest.approx(1500)
    assert st.rate_lps == pytest.approx(1500 / 600)


def test_out_of_order_reading_ignored():
    s = Settings(input_type=InputType.TOTALISER, zone=BNE)
    st = State()
    process_reading(st, s, 100, T0)
    process_reading(st, s, 200, T0 + MIN)
    r = process_reading(st, s, 150, T0 + 30_000)
    assert not r.accepted
    assert st.total_l == 100 and st.last_value == 200


def test_counter_long_gap_gives_volume_but_no_rate():
    s = Settings(input_type=InputType.TOTALISER, max_gap_s=600, zone=BNE)
    st = State()
    process_reading(st, s, 100, T0)
    process_reading(st, s, 300, T0 + 60 * MIN)
    assert st.total_l == 200
    assert st.rate_lps is None
    assert st.flowing is True
    process_reading(st, s, 300, T0 + 120 * MIN)
    assert st.flowing is False


def test_rate_integration_trapezoid_and_gap_cap():
    s = Settings(
        input_type=InputType.RATE,
        rate_lps=rate_factor("L/min"),
        max_gap_s=600,
        zone=BNE,
    )
    st = State()
    process_reading(st, s, 60, T0)  # 1 L/s
    assert st.total_l == 0
    assert st.rate_lps == pytest.approx(1.0)
    process_reading(st, s, 180, T0 + MIN)  # 3 L/s
    assert st.total_l == pytest.approx((1 + 3) / 2 * 60)
    # A one-hour gap integrates over the 10 minute cap only.
    process_reading(st, s, 180, T0 + 61 * MIN)
    assert st.total_l == pytest.approx(120 + 3 * 600)


def test_negative_rate_is_zero():
    s = Settings(input_type=InputType.RATE, zone=BNE)
    st = State()
    process_reading(st, s, -2, T0)
    assert st.rate_lps == 0
    assert st.flowing is False


def test_analog_mapping_and_fault():
    s = Settings(
        input_type=InputType.ANALOG,
        analog_min_input=4,
        analog_max_input=20,
        analog_min_lps=0,
        analog_max_lps=16,
        zone=BNE,
    )
    assert analog_to_lps(12, s) == pytest.approx(8)
    # In the clamp band below 4 mA: the minimum rate, not a fault.
    assert analog_to_lps(3.5, s) == 0

    st = State()
    process_reading(st, s, 12, T0)
    process_reading(st, s, 12, T0 + MIN)
    assert st.total_l == pytest.approx(480)

    r = process_reading(st, s, 0.2, T0 + 2 * MIN)
    assert r.fault_started
    assert st.sensor_fault == "under_range"
    assert st.rate_lps is None and st.flowing is None
    assert st.total_l == pytest.approx(480)  # no volume across a fault

    r = process_reading(st, s, 12, T0 + 3 * MIN)
    assert r.fault_cleared and st.sensor_fault is None
    # The first good reading after a fault does not integrate the fault gap.
    assert st.total_l == pytest.approx(480)
    process_reading(st, s, 12, T0 + 4 * MIN)
    assert st.total_l == pytest.approx(960)


def test_day_rollover():
    s = Settings(input_type=InputType.TOTALISER, zone=BNE)
    st = State()
    process_reading(st, s, 0, ms(2026, 11, 19, 22))
    process_reading(st, s, 50, ms(2026, 11, 19, 23))
    assert st.today_l == 50 and st.day == "2026-11-19"

    process_reading(st, s, 80, ms(2026, 11, 20, 1))
    assert st.yesterday_l == 50
    assert st.today_l == 30
    assert st.total_l == 80

    # Skip a whole day: yesterday had nothing.
    assert roll_day(st, ms(2026, 11, 22, 0, 5), BNE)
    assert st.yesterday_l == 0 and st.today_l == 0

    # A late tick for an earlier day never rolls back.
    assert not roll_day(st, ms(2026, 11, 21, 23), BNE)


def test_no_rollover_without_data():
    st = State()
    assert not roll_day(st, T0, BNE)
    assert st.today_l is None and st.yesterday_l is None


def test_day_boundary_follows_timezone():
    # 15:00 UTC is 01:00 the next day in Brisbane.
    s = Settings(input_type=InputType.TOTALISER, zone=BNE)
    st = State()
    process_reading(st, s, 0, ms(2026, 11, 19, 23, 30))
    utc_next = int(
        datetime.fromisoformat("2026-11-19T15:00:00+00:00").timestamp() * 1000
    )
    process_reading(st, s, 10, utc_next)
    assert st.day == "2026-11-20"
    assert st.yesterday_l == 0


def test_expire_rate():
    s = Settings(input_type=InputType.TOTALISER, max_gap_s=600, zone=BNE)
    st = State()
    assert not expire_rate(st, s, T0)
    process_reading(st, s, 0, T0)
    process_reading(st, s, 10, T0 + MIN)
    assert not expire_rate(st, s, T0 + 5 * MIN)
    assert expire_rate(st, s, T0 + 20 * MIN)
    assert st.rate_lps is None and st.flowing is None
    assert st.total_l == 10


def test_no_flow_due():
    s = Settings(input_type=InputType.TOTALISER, max_gap_s=3600, zone=BNE)
    hour = 60 * MIN
    st = State()
    # No data: never.
    assert not no_flow_due(st, s, T0, 1)

    process_reading(st, s, 0, T0)
    for i in range(1, 4):
        process_reading(st, s, 0, T0 + i * hour)
    assert no_flow_due(st, s, T0 + 3 * hour, 2)
    assert not no_flow_due(st, s, T0 + 3 * hour, 4)
    # Stopped reporting: no data is not no flow.
    assert not no_flow_due(st, s, T0 + 6 * hour, 2)

    process_reading(st, s, 5, T0 + 4 * hour)
    assert not no_flow_due(st, s, T0 + 4 * hour, 2)


def test_input_type_change_rebaselines():
    st = State()
    s1 = Settings(input_type=InputType.PULSE, zone=BNE)
    process_reading(st, s1, 500, T0)
    s2 = Settings(input_type=InputType.TOTALISER, zone=BNE)
    process_reading(st, s2, 10, T0 + MIN)
    assert st.total_l == 0
    assert st.baseline_key == InputType.TOTALISER
