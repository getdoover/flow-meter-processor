import logging
import time
from typing import Any

from pydoover.models import MessageCreateEvent
from pydoover.processor import Application

from .app_config import FlowMeterProcessorConfig
from .app_notifications import FlowMeterNotifications
from .app_tags import SENSOR_FAULT_DEFAULT_MESSAGE, FlowMeterProcessorTags
from .app_ui import FlowMeterProcessorUI
from .flow import (
    InputType,
    Settings,
    State,
    expire_rate,
    get_zone,
    no_flow_due,
    process_reading,
    rate_factor,
    roll_day,
    to_display,
    totaliser_factor,
    volume_factor,
)

log = logging.getLogger(__name__)

# State field -> the tag that carries it between invocations.
STATE_TAGS = {
    "last_ts": "last_reading_ts",
    "last_value": "state_last_value",
    "baseline_key": "state_input_type",
    "total_l": "total_volume_l",
    "today_l": "today_volume_l",
    "yesterday_l": "yesterday_volume_l",
    "day": "state_day",
    "first_ts": "first_reading_ts",
    "last_flow_ts": "last_flow_ts",
    "rate_lps": "flow_rate",
    "flowing": "flowing",
    "raw": "raw_reading",
    "sensor_fault": "sensor_fault",
    "fault_input": "state_fault_input",
}


def _num(element, default: float) -> float:
    """A numeric config value, or ``default`` when it is missing or unusable."""
    value = getattr(element, "value", None) if element is not None else None
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _str(element, default: str | None = None) -> str | None:
    value = getattr(element, "value", None) if element is not None else None
    return value if value not in (None, "") else default


def _bool(element) -> bool:
    return getattr(element, "value", None) is True if element is not None else False


def _now_ms() -> int:
    return int(time.time() * 1000)


class FlowMeterProcessorApplication(Application):
    config: FlowMeterProcessorConfig
    tags: FlowMeterProcessorTags
    ui: FlowMeterProcessorUI
    notifications: FlowMeterNotifications

    config_cls = FlowMeterProcessorConfig
    tags_cls = FlowMeterProcessorTags
    ui_cls = FlowMeterProcessorUI
    notifications_cls = FlowMeterNotifications

    async def setup(self):
        try:
            self._input_channel, self._input_path = self._parse_input_message_path(
                _str(self.config.input_message_path)
            )
        except ValueError as exc:
            log.error("Flow meter input not configured: %s", exc)
            self._input_channel, self._input_path = None, []

    # -- events ---------------------------------------------------------------

    async def on_message_create(self, event: MessageCreateEvent):
        if not self._input_channel or event.channel.name != self._input_channel:
            return

        value = self._extract_path_value(event.message.data or {}, self._input_path)
        if value is None:
            log.info(
                "No flow reading at %s in message on %s.",
                ".".join(self._input_path),
                event.channel.name,
            )
            return
        try:
            raw = self._coerce_reading(value)
        except (TypeError, ValueError):
            log.warning(
                "Flow reading at %s in message on %s is not numeric: %r",
                ".".join(self._input_path),
                event.channel.name,
                value,
            )
            return

        await self.handle_reading(raw, self._message_ts_ms(event))

    async def on_schedule(self, event):
        await self.handle_tick(_now_ms())

    async def on_deployment(self, event):
        # Republish the display copies, which follow the display units in config.
        state = self.load_state()
        if state.last_ts is None:
            return
        await self.save_state(state)

    # -- core -----------------------------------------------------------------

    async def handle_reading(self, raw: float, ts_ms: int):
        settings = self.settings()
        state = self.load_state()
        result = process_reading(state, settings, raw, ts_ms)
        if not result.accepted:
            log.info("Ignoring reading %s at %s: older than the last one.", raw, ts_ms)
            return
        if result.reset:
            log.warning("Counter dropped to %s: treated as a reset, re-baselined.", raw)
        if result.fault_started:
            log.warning("Analog input %s is below range: sensor fault.", raw)
        if result.fault_cleared:
            log.info("Analog input back in range at %s.", raw)

        await self.save_state(state)
        await self.check_alerts(state, settings, max(ts_ms, _now_ms()))

    async def handle_tick(self, now_ms: int):
        state = self.load_state()
        if state.last_ts is None:
            return  # no data yet: nothing to roll, expire or alert on
        settings = self.settings()
        roll_day(state, now_ms, settings.tz)
        expire_rate(state, settings, now_ms)
        await self.save_state(state)
        await self.check_alerts(state, settings, now_ms)

    def settings(self) -> Settings:
        c = self.config
        rate_in = rate_factor(_str(c.rate_units))
        display_rate = rate_factor(_str(c.display_rate_units))
        return Settings(
            input_type=_str(c.input_type, InputType.PULSE),
            litres_per_pulse=_num(c.litres_per_pulse, 1.0),
            totaliser_litres=totaliser_factor(_str(c.totaliser_units))
            * _num(c.totaliser_multiplier, 1.0),
            rate_lps=rate_in,
            analog_min_input=_num(c.analog_min_input, 4.0),
            analog_max_input=_num(c.analog_max_input, 20.0),
            analog_min_lps=_num(c.analog_min_rate, 0.0) * rate_in,
            analog_max_lps=_num(c.analog_max_rate, 10.0) * rate_in,
            max_gap_s=max(_num(c.max_gap_minutes, 60.0), 1.0) * 60.0,
            flowing_threshold_lps=_num(c.flowing_threshold, 0.0) * display_rate,
            zone=get_zone(_str(c.timezone)),
        )

    def load_state(self) -> State:
        state = State()
        for field, tag in STATE_TAGS.items():
            setattr(state, field, getattr(self.tags, tag).get())
        return state

    async def save_state(self, state: State):
        for field, tag in STATE_TAGS.items():
            await getattr(self.tags, tag).set(getattr(state, field))

        rate_units = rate_factor(_str(self.config.display_rate_units))
        vol_units = volume_factor(_str(self.config.display_volume_units))
        await self.tags.flow_rate_display.set(to_display(state.rate_lps, rate_units))
        await self.tags.total_volume_display.set(to_display(state.total_l, vol_units))
        await self.tags.today_volume_display.set(to_display(state.today_l, vol_units))
        await self.tags.yesterday_volume_display.set(
            to_display(state.yesterday_l, vol_units)
        )

        await self.tags.sensor_fault_hidden.set(state.sensor_fault is None)
        await self.tags.sensor_fault_message.set(self._fault_message(state))

    async def check_alerts(self, state: State, settings: Settings, now_ms: int):
        c = self.config

        if _bool(c.no_flow_alert_enabled):
            hours = _num(c.no_flow_alert_hours, 24.0)
            active = bool(self.tags.no_flow_alert_active.get())
            if no_flow_due(state, settings, now_ms, hours):
                if not active:
                    await self.notifications.no_flow.send(
                        f"No flow for over {hours:g} hours"
                    )
                    await self.tags.no_flow_alert_active.set(True)
            elif state.flowing and active:
                await self.tags.no_flow_alert_active.set(False)
        elif self.tags.no_flow_alert_active.get():
            await self.tags.no_flow_alert_active.set(False)

        if _bool(c.high_flow_alert_enabled):
            units = _str(c.display_rate_units, "L/s")
            threshold = _num(c.high_flow_threshold, 0.0)
            threshold_lps = threshold * rate_factor(units)
            active = bool(self.tags.high_flow_alert_active.get())
            rate = state.rate_lps
            if rate is not None and threshold_lps > 0 and rate > threshold_lps:
                if not active:
                    shown = to_display(rate, rate_factor(units))
                    await self.notifications.high_flow.send(
                        f"High flow: {shown:.3g} {units} (threshold {threshold:g} {units})"
                    )
                    await self.tags.high_flow_alert_active.set(True)
            elif rate is not None and active:
                await self.tags.high_flow_alert_active.set(False)
        elif self.tags.high_flow_alert_active.get():
            await self.tags.high_flow_alert_active.set(False)

    # -- helpers --------------------------------------------------------------

    def _fault_message(self, state: State) -> str:
        if state.fault_input is None:
            return SENSOR_FAULT_DEFAULT_MESSAGE
        units = _str(self.config.analog_input_units, "mA")
        return (
            f"Flow sensor signal below range ({state.fault_input:.2f} {units}) "
            "— check the sensor and its wiring"
        )

    @staticmethod
    def _message_ts_ms(event) -> int:
        try:
            return int(event.message.timestamp.timestamp() * 1000)
        except (AttributeError, TypeError, ValueError, OverflowError):
            # No usable message id: fall back to now.
            return _now_ms()

    @staticmethod
    def _parse_input_message_path(value: str | None) -> tuple[str, list[str]]:
        if not value or not isinstance(value, str):
            raise ValueError("Input Message Path must be a string like '$channel.key'.")

        path = value.strip()
        if not path.startswith("$"):
            raise ValueError("Input Message Path must start with '$channel'.")

        body = path[1:]
        if ":" in body:
            channel, raw_path = body.split(":", 1)
        elif "." in body:
            channel, raw_path = body.split(".", 1)
        else:
            raise ValueError(
                "Input Message Path must include a value path after the channel."
            )

        channel = channel.strip()
        parts = [part.strip() for part in raw_path.split(".") if part.strip()]
        if not channel or not parts:
            raise ValueError(
                "Input Message Path must include both a channel and value path."
            )
        return channel, parts

    @staticmethod
    def _extract_path_value(data: Any, path: list[str]):
        value = data
        for part in path:
            if isinstance(value, dict):
                value = value.get(part)
            elif isinstance(value, list):
                try:
                    value = value[int(part)]
                except (ValueError, IndexError):
                    return None
            else:
                return None

            if value is None:
                return None
        return value

    @staticmethod
    def _coerce_reading(value: Any) -> float:
        if isinstance(value, bool):
            raise TypeError("boolean is not a valid flow reading")
        return float(value)
