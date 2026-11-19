"""Flow maths and the state carried between processor invocations.

Pure functions over plain values so the conversion, integration, reset and
day-rollover rules can be tested without the Doover runtime. Every volume here
is litres and every rate is litres per second; the application converts to the
configured display units at the edges.
"""

import logging
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)


class InputType:
    PULSE = "Pulse Count"
    TOTALISER = "Totaliser"
    RATE = "Rate"
    ANALOG = "Analog"


INPUT_TYPES = [InputType.PULSE, InputType.TOTALISER, InputType.RATE, InputType.ANALOG]
COUNTER_INPUTS = (InputType.PULSE, InputType.TOTALISER)


# Litres per second in one of each rate unit.
RATE_UNITS_TO_LPS = {
    "L/s": 1.0,
    "L/min": 1.0 / 60.0,
    "kL/min": 1000.0 / 60.0,
    "m³/h": 1000.0 / 3600.0,
    "ML/day": 1_000_000.0 / 86400.0,
}
RATE_UNITS = list(RATE_UNITS_TO_LPS)

# Litres in one of each volume unit.
VOLUME_UNITS_TO_L = {
    "L": 1.0,
    "kL": 1000.0,
    "ML": 1_000_000.0,
}
VOLUME_UNITS = list(VOLUME_UNITS_TO_L)

# A totaliser can also count in cubic metres.
TOTALISER_UNITS_TO_L = {**VOLUME_UNITS_TO_L, "m³": 1000.0}
TOTALISER_UNITS = list(TOTALISER_UNITS_TO_L)

# Decimal places that read sensibly at each unit's scale.
RATE_UNIT_PRECISION = {"L/s": 2, "L/min": 1, "kL/min": 3, "m³/h": 2, "ML/day": 3}
VOLUME_UNIT_PRECISION = {"L": 0, "kL": 2, "ML": 3}

DEFAULT_TIMEZONE = "Australia/Brisbane"

# An analog loop this fraction of its input span below the minimum input is dead
# (under 1 mA on 4-20 mA); anything between that and the minimum reads as the
# minimum. Matches Analog Level Sensor Processor.
UNDER_RANGE_MARGIN = 3 / 16

SENSOR_FAULT_UNDER_RANGE = "under_range"


def rate_factor(units: str | None) -> float:
    """L/s in one of ``units``; unknown or missing units read as L/s."""
    return RATE_UNITS_TO_LPS.get(units or "", 1.0)


def volume_factor(units: str | None) -> float:
    """Litres in one of ``units``; unknown or missing units read as litres."""
    return VOLUME_UNITS_TO_L.get(units or "", 1.0)


def totaliser_factor(units: str | None) -> float:
    return TOTALISER_UNITS_TO_L.get(units or "", 1.0)


def to_display(value: float | None, factor: float) -> float | None:
    """Canonical value -> display units. None stays None, never zero."""
    if value is None:
        return None
    return value / factor


def get_zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("Unknown timezone %r, using %s", name, DEFAULT_TIMEZONE)
        return ZoneInfo(DEFAULT_TIMEZONE)


def local_date(ts_ms: int, zone: ZoneInfo) -> date:
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).astimezone(zone).date()


@dataclass
class Settings:
    """Everything the maths needs from the deployment config, in canonical units."""

    input_type: str = InputType.PULSE
    litres_per_pulse: float = 1.0
    # Litres per raw totaliser count (units factor x multiplier).
    totaliser_litres: float = 1.0
    # L/s per raw rate reading.
    rate_lps: float = 1.0
    analog_min_input: float = 4.0
    analog_max_input: float = 20.0
    analog_min_lps: float = 0.0
    analog_max_lps: float = 1.0
    max_gap_s: float = 3600.0
    flowing_threshold_lps: float = 0.0
    zone: ZoneInfo | None = None

    @property
    def tz(self) -> ZoneInfo:
        return self.zone or ZoneInfo(DEFAULT_TIMEZONE)

    @property
    def baseline_key(self) -> str:
        """Changes whenever the meaning of ``State.last_value`` would."""
        return self.input_type


@dataclass
class State:
    """Carried between invocations in tags. None means "not known yet"."""

    # Timestamp (ms) of the last reading that was accepted.
    last_ts: int | None = None
    # Counter inputs: the last raw count. Rate/analog: the last good rate (L/s).
    last_value: float | None = None
    baseline_key: str | None = None
    total_l: float | None = None
    today_l: float | None = None
    yesterday_l: float | None = None
    # ISO local date today_l belongs to.
    day: str | None = None
    first_ts: int | None = None
    last_flow_ts: int | None = None
    rate_lps: float | None = None
    flowing: bool | None = None
    raw: float | None = None
    sensor_fault: str | None = None
    # The last under-range analog input, for the warning text.
    fault_input: float | None = None

    @classmethod
    def field_names(cls) -> list[str]:
        return [f.name for f in fields(cls)]


def roll_day(state: State, now_ms: int, zone: ZoneInfo) -> bool:
    """Move today's volume to yesterday when the local day has changed.

    Returns True when it rolled. Does nothing before the first reading, so a
    meter that has never reported keeps null Today / Yesterday values. A time
    earlier than the stored day (a late message after the rollover) never
    rolls back.
    """
    if state.total_l is None:
        return False
    today = local_date(now_ms, zone)
    if state.day is None:
        state.day = today.isoformat()
        return False
    try:
        stored = date.fromisoformat(state.day)
    except ValueError:
        stored = None
    if stored is not None and today <= stored:
        return False
    if stored is not None and today - stored == timedelta(days=1):
        state.yesterday_l = state.today_l or 0.0
    else:
        # No reading at all yesterday (or an unreadable stored day).
        state.yesterday_l = 0.0
    state.today_l = 0.0
    state.day = today.isoformat()
    return True


def is_under_range(raw: float, s: Settings) -> bool:
    span = abs(s.analog_max_input - s.analog_min_input)
    return raw < s.analog_min_input - span * UNDER_RANGE_MARGIN


def analog_to_lps(raw: float, s: Settings) -> float:
    """Map an analog input linearly onto the configured rate range (L/s).

    Inside the under-range margin the input reads as the minimum; a rate is
    never negative.
    """
    span = s.analog_max_input - s.analog_min_input
    if span == 0:
        return max(s.analog_min_lps, 0.0)
    reading = max(raw, s.analog_min_input)
    frac = (reading - s.analog_min_input) / span
    rate = s.analog_min_lps + frac * (s.analog_max_lps - s.analog_min_lps)
    return max(rate, 0.0)


@dataclass
class Result:
    accepted: bool
    added_l: float = 0.0
    reset: bool = False
    fault_started: bool = False
    fault_cleared: bool = False


def process_reading(state: State, s: Settings, raw: float, ts_ms: int) -> Result:
    """Fold one raw reading into ``state`` (mutated in place)."""
    if state.last_ts is not None and ts_ms < state.last_ts:
        # Out of order: a late counter reading would look like a reset.
        return Result(accepted=False)

    roll_day(state, ts_ms, s.tz)

    if state.baseline_key != s.baseline_key:
        # The input type changed: the stored value means something else now.
        state.last_value = None
        state.baseline_key = s.baseline_key

    dt = None if state.last_ts is None else (ts_ms - state.last_ts) / 1000.0
    result = Result(accepted=True)
    state.raw = raw
    # Whether this reading was compared against a previous counter value.
    compared = False

    if s.input_type in COUNTER_INPUTS:
        per_count = (
            s.litres_per_pulse
            if s.input_type == InputType.PULSE
            else s.totaliser_litres
        )
        rate = None
        added = 0.0
        if state.last_value is None:
            pass  # first reading: baseline only
        elif raw < state.last_value:
            # Counter reset or rollover: re-baseline, never negative volume.
            result.reset = True
        else:
            compared = True
            added = (raw - state.last_value) * per_count
            if dt is not None and 0 < dt <= s.max_gap_s:
                rate = added / dt
        state.last_value = raw
        state.rate_lps = rate
        _clear_fault(state, result)
    else:
        if s.input_type == InputType.ANALOG and is_under_range(raw, s):
            # A dead loop is a sensor fault, not zero flow: nothing integrates
            # across it and the rate is unknown.
            result.fault_started = state.sensor_fault is None
            state.sensor_fault = SENSOR_FAULT_UNDER_RANGE
            state.fault_input = raw
            state.last_value = None
            state.rate_lps = None
            state.flowing = None
            state.last_ts = ts_ms
            if state.first_ts is None:
                state.first_ts = ts_ms
            return result

        if s.input_type == InputType.ANALOG:
            rate = analog_to_lps(raw, s)
        else:
            rate = max(raw * s.rate_lps, 0.0)

        added = 0.0
        if state.last_value is not None and dt is not None and dt > 0:
            # Trapezoid between the two readings, over at most the max gap.
            added = (state.last_value + rate) / 2.0 * min(dt, s.max_gap_s)
        state.last_value = rate
        state.rate_lps = rate
        _clear_fault(state, result)

    result.added_l = added
    if state.total_l is None:
        state.total_l = 0.0
        state.today_l = 0.0
        state.day = local_date(ts_ms, s.tz).isoformat()
    state.total_l += added
    state.today_l = (state.today_l or 0.0) + added

    if state.rate_lps is not None:
        state.flowing = state.rate_lps > s.flowing_threshold_lps
    elif added > 0:
        # A counter that moved over a gap too long to give a rate.
        state.flowing = True
    elif compared:
        # A counter that held still over a long gap.
        state.flowing = False
    else:
        # First reading, a reset, or a re-baseline: not known yet.
        state.flowing = None
    if state.flowing:
        state.last_flow_ts = ts_ms

    if state.first_ts is None:
        state.first_ts = ts_ms
    state.last_ts = ts_ms
    return result


def _clear_fault(state: State, result: Result):
    if state.sensor_fault is not None:
        result.fault_cleared = True
    state.sensor_fault = None
    state.fault_input = None


def expire_rate(state: State, s: Settings, now_ms: int) -> bool:
    """Clear the rate once no reading has arrived within the max gap.

    The last rate would otherwise read as live long after the edge went quiet.
    Volumes are kept. Returns True when it cleared something.
    """
    if state.last_ts is None:
        return False
    if (now_ms - state.last_ts) / 1000.0 <= s.max_gap_s:
        return False
    if state.rate_lps is None and state.flowing is None:
        return False
    state.rate_lps = None
    state.flowing = None
    return True


def no_flow_due(state: State, s: Settings, now_ms: int, hours: float) -> bool:
    """True when the meter is reporting but has not flowed for ``hours``.

    Never with no data: it needs a reading inside the max gap (so the meter is
    known to be reporting now), no sensor fault, and readings going back at
    least ``hours``.
    """
    if hours is None or hours <= 0:
        return False
    if state.last_ts is None or state.first_ts is None:
        return False
    if state.sensor_fault is not None:
        return False
    if (now_ms - state.last_ts) / 1000.0 > s.max_gap_s:
        return False
    if state.flowing:
        return False
    window_ms = hours * 3600 * 1000
    reference = state.last_flow_ts if state.last_flow_ts is not None else state.first_ts
    return now_ms - reference > window_ms
