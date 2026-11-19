from pydoover import ui

from .app_tags import FlowMeterProcessorTags as T
from .flow import (
    RATE_UNIT_PRECISION,
    VOLUME_UNIT_PRECISION,
    InputType,
    rate_factor,
)


class FlowMeterProcessorUI(ui.UI):
    # Shown while sensor_fault is set. The schema is published once, so the
    # visibility and the text (which carries the reading) are bound to tags.
    sensor_fault_warning = ui.WarningIndicator(
        T.sensor_fault_message,
        name="sensor_fault_warning",
        hidden=T.sensor_fault_hidden,
        can_cancel=False,
        position=1,
    )

    flow_rate = ui.NumericVariable(
        "Flow Rate",
        name="flow_rate",
        units="L/s",
        value=T.flow_rate_display,
        precision=2,
        position=10,
    )
    today = ui.NumericVariable(
        "Today",
        name="today_volume",
        units="L",
        value=T.today_volume_display,
        precision=0,
        position=20,
    )
    yesterday = ui.NumericVariable(
        "Yesterday",
        name="yesterday_volume",
        units="L",
        value=T.yesterday_volume_display,
        precision=0,
        position=30,
    )
    total = ui.NumericVariable(
        "Total",
        name="total_volume",
        units="L",
        value=T.total_volume_display,
        precision=0,
        position=40,
    )
    last_flowed = ui.Timestamp(
        "Last Flowed",
        name="last_flowed",
        value=T.last_flow_ts,
        precision="minute",
        position=50,
    )

    async def setup(self):
        rate_units = self.config.display_rate_units.value or "L/s"
        volume_units = self.config.display_volume_units.value or "L"

        self.flow_rate.units = rate_units
        self.flow_rate.precision = RATE_UNIT_PRECISION.get(rate_units, 2)
        for element in (self.today, self.yesterday, self.total):
            element.units = volume_units
            element.precision = VOLUME_UNIT_PRECISION.get(volume_units, 0)

        # An analog meter has a known full scale, so its rate reads as a gauge.
        if self.config.input_type.value == InputType.ANALOG:
            factor = rate_factor(self.config.rate_units.value) / rate_factor(rate_units)
            top = float(self.config.analog_max_rate.value or 0) * factor
            if top > 0:
                self.flow_rate.form = ui.Widget.radial
                self.flow_rate.ranges = [ui.Range("Flow", 0, top, ui.Colour.blue)]


def export():
    from pathlib import Path

    FlowMeterProcessorUI(None, None, None).export(
        Path(__file__).parents[2] / "doover_config.json",
        "flow_meter_processor",
    )
