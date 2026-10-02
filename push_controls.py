"""Independent default-off controls; registration alone never enables sending."""

import os


def outbox_enabled():
    return (
        os.environ.get("PUSH_OUTBOX_ENABLED") == "1"
        and os.environ.get("PUSH_DEVICE_REGISTRATION_ENABLED") == "1"
    )


def delivery_enabled():
    return outbox_enabled() and os.environ.get("PUSH_DELIVERY_ENABLED") == "1"
