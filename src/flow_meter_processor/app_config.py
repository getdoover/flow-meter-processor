import zoneinfo
from pathlib import Path

from pydoover import config
from pydoover.config import ApplicationDefaultOpen, ApplicationPosition
from pydoover.processor import ManySubscriptionConfig, ScheduleConfig, TimezoneConfig

from .flow import (
    DEFAULT_TIMEZONE,
    INPUT_TYPES,
    RATE_UNITS,
    TOTALISER_UNITS,
    VOLUME_UNITS,
    InputType,
)


class InputUnits:
    MILLIAMPS = "mA"
    VOLTS = "V"


_PULSE = config.equal("input_type", InputType.PULSE)
_TOTALISER = config.equal("input_type", InputType.TOTALISER)
_RATE = config.equal("input_type", InputType.RATE)
_ANALOG = config.equal("input_type", InputType.ANALOG)
_RATE_OR_ANALOG = config.one_of("input_type", [InputType.RATE, InputType.ANALOG])

# TimezoneConfig offers whatever zoneinfo finds on the exporting machine, which
# can include non-IANA entries such as `localtime` that the Lambda runtime does
# not have. Offer the real zones only.
_TIMEZONES = sorted(
    z
    for z in zoneinfo.available_timezones()
    if z not in ("localtime", "posixrules", "Factory")
)


# Every 15 minutes: rolls Today into Yesterday at local midnight, clears a rate
# that has gone stale, and checks the no-flow notification, even while no
# readings arrive.
DEFAULT_SCHEDULE = "rate(15 minutes)"


class FlowMeterProcessorConfig(config.Schema):
    input_type = config.Enum(
        "Input Type",
        name="input_type",
        choices=INPUT_TYPES,
        default=InputType.PULSE,
        description=(
            "What the raw reading is. Pulse Count: a cumulative pulse counter. "
            "Totaliser: a cumulative volume reading. Rate: an instantaneous flow "
            "rate. Analog: a 4-20 mA (or voltage) signal mapped onto a flow rate range."
        ),
        position=0,
    )
    input_message_path = config.String(
        "Input Message Path",
        name="input_message_path",
        description=(
            "Message path holding the raw reading. Use '$channel.path.to.value', "
            "for example '$io_state.flow_1'. The channel must be in the subscription list."
        ),
        default="$io_state.flow_1",
        position=1,
    )

    litres_per_pulse = config.Number(
        "Litres per Pulse",
        name="litres_per_pulse",
        description="Volume each pulse represents, in litres.",
        default=1.0,
        minimum=0.0,
        show_if=_PULSE,
        position=2,
    )

    totaliser_units = config.Enum(
        "Totaliser Units",
        name="totaliser_units",
        choices=TOTALISER_UNITS,
        default="L",
        description="Units the totaliser counts in.",
        show_if=_TOTALISER,
        position=3,
    )
    totaliser_multiplier = config.Number(
        "Totaliser Multiplier",
        name="totaliser_multiplier",
        description=(
            "Extra scale applied to the totaliser reading before its units, e.g. 10 "
            "for a register that counts tens of litres. Leave at 1 for a plain reading."
        ),
        default=1.0,
        minimum=0.0,
        show_if=_TOTALISER,
        position=4,
    )

    rate_units = config.Enum(
        "Input Rate Units",
        name="rate_units",
        choices=RATE_UNITS,
        default="L/s",
        description=(
            "Units of the incoming rate reading (Rate), or of the rate range the "
            "analog signal maps onto (Analog)."
        ),
        show_if=_RATE_OR_ANALOG,
        position=5,
    )

    analog_input_units = config.Enum(
        "Analog Input Units",
        name="analog_input_units",
        choices=[InputUnits.MILLIAMPS, InputUnits.VOLTS],
        default=InputUnits.MILLIAMPS,
        description="Units of the raw analog signal.",
        show_if=_ANALOG,
        position=6,
    )
    analog_min_input = config.Number(
        "Analog Minimum Input",
        name="analog_min_input",
        description=(
            "Signal at the minimum rate, in the analog input units. A signal well "
            "below this (under 1 mA on 4-20 mA) is a sensor fault, not zero flow."
        ),
        default=4.0,
        show_if=_ANALOG,
        position=7,
    )
    analog_max_input = config.Number(
        "Analog Maximum Input",
        name="analog_max_input",
        description="Signal at the maximum rate, in the analog input units.",
        default=20.0,
        show_if=_ANALOG,
        position=8,
    )
    analog_min_rate = config.Number(
        "Analog Minimum Rate",
        name="analog_min_rate",
        description="Flow rate at the minimum input, in the input rate units.",
        default=0.0,
        show_if=_ANALOG,
        position=9,
    )
    analog_max_rate = config.Number(
        "Analog Maximum Rate",
        name="analog_max_rate",
        description="Flow rate at the maximum input, in the input rate units.",
        default=10.0,
        show_if=_ANALOG,
        position=10,
    )

    max_gap_minutes = config.Number(
        "Max Reading Gap (minutes)",
        name="max_gap_minutes",
        description=(
            "The longest gap between readings that still counts as continuous. A "
            "Rate or Analog reading is integrated over at most this long, a counter "
            "gives a flow rate only across a gap this short, and the flow rate is "
            "cleared once no reading has arrived for this long. Set it above the "
            "edge's reporting interval."
        ),
        default=60.0,
        minimum=1.0,
        position=11,
    )

    display_rate_units = config.Enum(
        "Display Rate Units",
        name="display_rate_units",
        choices=RATE_UNITS,
        default="L/s",
        description="Units to show the flow rate in. The flow_rate tag stays in L/s.",
        position=12,
    )
    display_volume_units = config.Enum(
        "Display Volume Units",
        name="display_volume_units",
        choices=VOLUME_UNITS,
        default="L",
        description=(
            "Units to show Today, Yesterday and Total in. The *_volume_l tags stay in litres."
        ),
        position=13,
    )
    flowing_threshold = config.Number(
        "Flowing Threshold",
        name="flowing_threshold",
        description="A flow rate above this, in the display rate units, counts as flowing.",
        default=0.0,
        minimum=0.0,
        position=14,
    )
    timezone = TimezoneConfig(
        description="Local timezone for the Today / Yesterday day boundary.",
        default=DEFAULT_TIMEZONE,
        position=15,
    )
    timezone.choices = _TIMEZONES

    no_flow_alert_enabled = config.Boolean(
        "No Flow Alert",
        name="no_flow_alert_enabled",
        description=(
            "Notify when the meter is reporting but nothing has flowed for longer "
            "than the No Flow Hours. Only for a meter that is expected to flow."
        ),
        default=False,
        position=16,
    )
    no_flow_alert_hours = config.Number(
        "No Flow Hours",
        name="no_flow_alert_hours",
        description="Hours without flow before the No Flow Alert is sent.",
        default=24.0,
        minimum=0.0,
        show_if=config.equal("no_flow_alert_enabled", True),
        position=17,
    )
    high_flow_alert_enabled = config.Boolean(
        "High Flow Alert",
        name="high_flow_alert_enabled",
        description="Notify when the flow rate rises above the High Flow Threshold.",
        default=False,
        position=18,
    )
    high_flow_threshold = config.Number(
        "High Flow Threshold",
        name="high_flow_threshold",
        description="Flow rate, in the display rate units, above which the High Flow Alert is sent.",
        default=10.0,
        minimum=0.0,
        show_if=config.equal("high_flow_alert_enabled", True),
        position=19,
    )

    subscription = ManySubscriptionConfig(
        default=["io_state"],
        advanced=True,
        position=20,
    )
    schedule = ScheduleConfig(
        default=DEFAULT_SCHEDULE,
        description=(
            "How often to check for the day rollover, a stale rate and the no-flow "
            "alert while no readings arrive."
        ),
        advanced=True,
        position=21,
    )

    position = ApplicationPosition(default=50, position=22)
    default_open = ApplicationDefaultOpen(default=True, position=23)


def export():
    FlowMeterProcessorConfig.export(
        Path(__file__).parents[2] / "doover_config.json",
        "flow_meter_processor",
    )
