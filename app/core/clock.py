from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings


def now_local_with_source() -> tuple[datetime, str]:
    tz_name = settings.app.timezone
    try:
        return datetime.now(ZoneInfo(tz_name)), "zoneinfo"
    except ZoneInfoNotFoundError:
        # Fallback to system local timezone, preserving correct wall-clock time.
        return datetime.now(timezone.utc).astimezone(), "fallback_system_local"


def now_local() -> datetime:
    local_now, _ = now_local_with_source()
    return local_now
