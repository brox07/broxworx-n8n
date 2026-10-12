from datetime import datetime, timezone
from time import struct_time


def struct_to_datetime(value: struct_time | None) -> datetime | None:
    """feedparser normalizes feed dates to UTC struct_time; turn that into an aware datetime."""
    if value is None:
        return None
    return datetime(*value[:6], tzinfo=timezone.utc)
