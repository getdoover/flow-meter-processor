from pathlib import Path

from pydoover import notifications as n


class FlowMeterNotifications(n.Notifications):
    no_flow = n.Notification(
        "No flow for longer than expected",
        display_name="No flow",
        description=(
            "The meter is reporting but nothing has flowed for longer than the "
            "configured No Flow Hours. Only sent when No Flow Alert is enabled."
        ),
        severity=n.NotificationSeverity.Warn,
    )
    high_flow = n.Notification(
        "Flow rate above the high flow threshold",
        display_name="High flow",
        description=(
            "The flow rate rose above the configured High Flow Threshold. Only "
            "sent when High Flow Alert is enabled."
        ),
        severity=n.NotificationSeverity.Warn,
    )


def export():
    FlowMeterNotifications.export(
        Path(__file__).parents[2] / "doover_config.json",
        "flow_meter_processor",
    )
