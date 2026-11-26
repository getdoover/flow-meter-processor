"""The processor handler: message paths, tags, display copies, no-data, alerts."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from flow_meter_processor.app_config import FlowMeterProcessorConfig
from flow_meter_processor.app_tags import FlowMeterProcessorTags
from flow_meter_processor.application import (
    FlowMeterProcessorApplication,
    io_points,
    point_label,
    resolve_input_path,
)

APP_KEY = "flow_meter_processor_1"


class Value:
    def __init__(self, value):
        self.value = value


def make_config(**overrides):
    values = {
        "input_type": "Totaliser",
        "input_message_path": "$io_state.flow_volume",
        "litres_per_pulse": 1.0,
        "totaliser_units": "kL",
        "totaliser_multiplier": 1.0,
        "rate_units": "L/s",
        "analog_input_units": "mA",
        "analog_min_input": 4.0,
        "analog_max_input": 20.0,
        "analog_min_rate": 0.0,
        "analog_max_rate": 10.0,
        "max_gap_minutes": 60.0,
        "display_rate_units": "kL/min",
        "display_volume_units": "kL",
        "flowing_threshold": 0.0,
        "timezone": "Australia/Brisbane",
        "no_flow_alert_enabled": False,
        "no_flow_alert_hours": 24.0,
        "high_flow_alert_enabled": False,
        "high_flow_threshold": 10.0,
    }
    values.update(overrides)
    return SimpleNamespace(**{k: Value(v) for k, v in values.items()})


class FakeTag:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    async def set(self, value):
        self.value = value


class FakeTags:
    """Every declared tag, starting at its declared default."""

    def __init__(self):
        for name, decl in FlowMeterProcessorTags.__tag_declarations__.items():
            setattr(self, name, FakeTag(decl.template.default))


class FakeNotification:
    def __init__(self):
        self.sent = []

    async def send(self, message=None, **kwargs):
        self.sent.append(message)


def make_app(**config):
    app = object.__new__(FlowMeterProcessorApplication)
    app.app_key = APP_KEY
    app.config = make_config(**config)
    app.tags = FakeTags()
    app.notifications = SimpleNamespace(
        no_flow=FakeNotification(), high_flow=FakeNotification()
    )
    return app


def event(data, ts_ms, channel="io_state"):
    ts = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
    return SimpleNamespace(
        channel=SimpleNamespace(name=channel),
        message=SimpleNamespace(data=data, timestamp=ts),
    )


T0 = int(datetime(2026, 11, 19, 0, 0, tzinfo=UTC).timestamp() * 1000)
MIN = 60_000


def test_fake_tags_cover_every_declared_tag():
    app = make_app()
    for name in ("flow_rate", "total_volume_l", "state_day", "high_flow_alert_active"):
        assert hasattr(app.tags, name)


def test_parse_input_message_path():
    parse = FlowMeterProcessorApplication._parse_input_message_path
    assert parse("$io_state.flow_volume") == ("io_state", ["flow_volume"])
    assert parse("$io_state:meters.0.total") == ("io_state", ["meters", "0", "total"])


@pytest.mark.asyncio
async def test_bad_path_is_ignored_not_raised():
    app = make_app(input_message_path="nonsense")
    await app.setup()
    await app.on_message_create(event({"flow_volume": 1}, T0))
    assert app.tags.raw_reading.value is None


@pytest.mark.asyncio
async def test_totaliser_kl_end_to_end():
    app = make_app()
    await app.setup()
    await app.on_message_create(event({"flow_volume": 100.0}, T0))
    assert app.tags.raw_reading.value == 100.0
    assert app.tags.total_volume_l.value == 0
    assert app.tags.flow_rate.value is None

    await app.on_message_create(event({"flow_volume": 101.5}, T0 + MIN))
    assert app.tags.total_volume_l.value == pytest.approx(1500)
    assert app.tags.total_volume_display.value == pytest.approx(1.5)
    assert app.tags.today_volume_display.value == pytest.approx(1.5)
    assert app.tags.flow_rate.value == pytest.approx(25.0)  # L/s
    assert app.tags.flow_rate_display.value == pytest.approx(1.5)  # kL/min
    assert app.tags.flowing.value is True
    assert app.tags.last_flow_ts.value == T0 + MIN
    assert app.tags.sensor_fault_hidden.value is True


@pytest.mark.asyncio
async def test_other_channel_and_missing_value_ignored():
    app = make_app()
    await app.setup()
    await app.on_message_create(event({"flow_volume": 5}, T0, channel="other"))
    await app.on_message_create(event({"something_else": 5}, T0))
    await app.on_message_create(event({"flow_volume": "abc"}, T0))
    await app.on_message_create(event({"flow_volume": True}, T0))
    assert app.tags.raw_reading.value is None
    assert app.tags.total_volume_l.value is None


@pytest.mark.asyncio
async def test_schedule_with_no_data_does_nothing():
    app = make_app(no_flow_alert_enabled=True, no_flow_alert_hours=0.001)
    await app.setup()
    await app.on_schedule(None)
    assert app.tags.today_volume_l.value is None
    assert app.tags.yesterday_volume_l.value is None
    assert app.notifications.no_flow.sent == []


@pytest.mark.asyncio
async def test_analog_fault_shows_warning():
    app = make_app(
        input_type="Analog",
        input_message_path="$io_state.ai_1",
        rate_units="L/s",
        display_rate_units="L/s",
    )
    await app.setup()
    await app.on_message_create(event({"ai_1": 12.0}, T0))
    assert app.tags.flow_rate.value == pytest.approx(5.0)
    await app.on_message_create(event({"ai_1": 0.1}, T0 + MIN))
    assert app.tags.sensor_fault.value == "under_range"
    assert app.tags.sensor_fault_hidden.value is False
    assert "0.10 mA" in app.tags.sensor_fault_message.value
    assert app.tags.flow_rate.value is None
    assert app.tags.flow_rate_display.value is None


@pytest.mark.asyncio
async def test_high_flow_alert_once_and_rearms():
    app = make_app(
        input_type="Rate",
        input_message_path="$io_state.rate",
        rate_units="L/s",
        display_rate_units="L/s",
        high_flow_alert_enabled=True,
        high_flow_threshold=10.0,
    )
    await app.setup()
    await app.on_message_create(event({"rate": 12}, T0))
    await app.on_message_create(event({"rate": 15}, T0 + MIN))
    assert len(app.notifications.high_flow.sent) == 1
    await app.on_message_create(event({"rate": 5}, T0 + 2 * MIN))
    assert app.tags.high_flow_alert_active.value is False
    await app.on_message_create(event({"rate": 11}, T0 + 3 * MIN))
    assert len(app.notifications.high_flow.sent) == 2


@pytest.mark.asyncio
async def test_high_flow_disabled_by_default():
    app = make_app(input_type="Rate", input_message_path="$io_state.rate")
    await app.setup()
    await app.on_message_create(event({"rate": 1000}, T0))
    assert app.notifications.high_flow.sent == []


@pytest.mark.asyncio
async def test_display_units_refresh_on_deployment():
    app = make_app()
    await app.setup()
    await app.on_message_create(event({"flow_volume": 1.0}, T0))
    await app.on_message_create(event({"flow_volume": 3.0}, T0 + MIN))
    app.config.display_volume_units = Value("L")
    await app.on_deployment(None)
    assert app.tags.total_volume_display.value == pytest.approx(2000)


def test_config_defaults_load_for_hidden_fields():
    """A Totaliser install's stored config carries no pulse or analog keys;
    those elements must still resolve to their defaults."""
    cfg = FlowMeterProcessorConfig()
    cfg._inject_deployment_config(
        {
            "input_type": "Totaliser",
            "input_message_path": "$io_state.flow_volume",
            "totaliser_units": "kL",
            "display_rate_units": "kL/min",
            "display_volume_units": "kL",
        }
    )
    assert cfg.input_type.value == "Totaliser"
    assert cfg.totaliser_units.value == "kL"
    assert cfg.litres_per_pulse.value == 1.0
    assert cfg.max_gap_minutes.value == 60.0
    assert cfg.timezone.value == "Australia/Brisbane"
    assert cfg.no_flow_alert_enabled.value is False


# -- automatic input address and io_points ------------------------------------


def test_blank_path_resolves_to_automatic_address():
    assert resolve_input_path("", APP_KEY) == (f"$io_state.{APP_KEY}.flow", True)
    assert resolve_input_path("  ", APP_KEY) == (f"$io_state.{APP_KEY}.flow", True)
    assert resolve_input_path(None, APP_KEY) == (f"$io_state.{APP_KEY}.flow", True)
    assert resolve_input_path("", None) == (None, True)


def test_explicit_path_wins():
    assert resolve_input_path(" $io_state.flow_1 ", APP_KEY) == (
        "$io_state.flow_1",
        False,
    )
    assert resolve_input_path("$on_dm_event.x", APP_KEY) == ("$on_dm_event.x", False)


def test_point_labels_fit_the_input_type():
    assert point_label("Pulse Count") == "Flow meter pulse count"
    assert point_label("Totaliser") == "Flow meter total"
    assert point_label("Rate") == "Flow rate"
    assert point_label("Analog", "mA") == "Flow meter signal (mA)"
    assert point_label("Analog", "V") == "Flow meter signal (V)"
    assert point_label(None) == "Flow meter"
    for label in (point_label(t) for t in ("Pulse Count", "Totaliser", "Rate")):
        assert "_" not in label


def test_io_points_shape():
    assert io_points("$io_state.k.flow", True, "Flow rate") == {
        "version": 1,
        "points": [
            {
                "id": "flow",
                "label": "Flow rate",
                "kind": "input",
                "path": "$io_state.k.flow",
                "auto": True,
            }
        ],
    }
    assert io_points(None, True, "Flow rate") == {"version": 1, "points": []}


@pytest.mark.asyncio
async def test_blank_path_reads_nested_automatic_address():
    app = make_app(input_message_path="")
    await app.setup()
    await app.on_message_create(event({APP_KEY: {"flow": 100.0, "other": 1}}, T0))
    assert app.tags.raw_reading.value == 100.0
    # The old flat key is not the automatic address.
    await app.on_message_create(event({"flow": 200.0}, T0 + MIN))
    assert app.tags.raw_reading.value == 100.0
    await app.on_message_create(event({APP_KEY: {"flow": 101.0}}, T0 + 2 * MIN))
    assert app.tags.raw_reading.value == 101.0


@pytest.mark.asyncio
async def test_io_points_published_on_deployment_and_schedule_when_changed():
    app = make_app(input_message_path="", input_type="Rate")
    await app.setup()
    await app.on_deployment(None)
    assert app.tags.io_points.value == {
        "version": 1,
        "points": [
            {
                "id": "flow",
                "label": "Flow rate",
                "kind": "input",
                "path": f"$io_state.{APP_KEY}.flow",
                "auto": True,
            }
        ],
    }

    writes = []
    original = app.tags.io_points.set

    async def counting(value):
        writes.append(value)
        await original(value)

    app.tags.io_points.set = counting
    await app.on_schedule(None)
    assert writes == []  # unchanged, so not written again

    app.config.input_message_path.value = "$io_state.flow_1"
    await app.setup()
    await app.on_schedule(None)
    assert len(writes) == 1
    assert writes[0]["points"][0]["path"] == "$io_state.flow_1"
    assert writes[0]["points"][0]["auto"] is False
