from pydoover.processor import run_app

from .application import FlowMeterProcessorApplication


def handler(event, context):
    """Lambda handler entry point."""
    return run_app(
        FlowMeterProcessorApplication(),
        event,
        context,
    )
