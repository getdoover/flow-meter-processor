from pydoover.tags import Tag, Tags

SENSOR_FAULT_DEFAULT_MESSAGE = (
    "Flow sensor signal below range — check the sensor and its wiring"
)


class FlowMeterProcessorTags(Tags):
    # -- published values (canonical units: L/s and litres) -------------------
    flow_rate = Tag("number", default=None, live=True)
    # flow_rate in the configured display rate units, for this app's own UI.
    flow_rate_display = Tag("number", default=None, live=True)
    total_volume_l = Tag("number", default=None)
    today_volume_l = Tag("number", default=None)
    yesterday_volume_l = Tag("number", default=None)
    # The volumes in the configured display volume units.
    total_volume_display = Tag("number", default=None)
    today_volume_display = Tag("number", default=None)
    yesterday_volume_display = Tag("number", default=None)
    flowing = Tag("boolean", default=None)
    # ms since epoch of the last reading that showed flow.
    last_flow_ts = Tag("number", default=None)
    # The last raw reading as received (count, volume, rate, mA or V).
    raw_reading = Tag("number", default=None)
    # "under_range" while an analog input is in fault, None otherwise.
    sensor_fault = Tag("string", default=None, live=True)
    sensor_fault_hidden = Tag("boolean", default=True)
    sensor_fault_message = Tag("string", default=SENSOR_FAULT_DEFAULT_MESSAGE)

    # -- state carried between invocations -----------------------------------
    # ms since epoch of the last accepted reading.
    last_reading_ts = Tag("number", default=None)
    # ms since epoch of the first reading this install accepted.
    first_reading_ts = Tag("number", default=None)
    # Last raw count (Pulse Count / Totaliser) or last rate in L/s (Rate / Analog).
    state_last_value = Tag("number", default=None)
    # Input Type state_last_value was recorded under.
    state_input_type = Tag("string", default=None)
    # Local date (ISO) today_volume_l belongs to.
    state_day = Tag("string", default=None)
    # Last under-range analog input, for the warning text.
    state_fault_input = Tag("number", default=None)
    no_flow_alert_active = Tag("boolean", default=False)
    # The input this install reads and where: {"version": 1, "points": [...]}.
    io_points = Tag("object", default=None)
    high_flow_alert_active = Tag("boolean", default=False)
