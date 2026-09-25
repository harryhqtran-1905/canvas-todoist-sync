"""Pure date and priority helpers. No network or file I/O."""

from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo


def normalize_todoist_due_date(due_value: Optional[str]) -> Optional[str]:
    """Extract YYYY-MM-DD from Todoist date or datetime strings."""
    if not due_value:
        return None
    return due_value[:10]


def parse_due_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse a Todoist/Canvas datetime string into an aware UTC datetime.

    Handles 'Z', '+00:00', and naive variants; returns None for empty or
    date-only values.
    """
    if not value or "T" not in value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_due_date(due_dt: datetime, tz: ZoneInfo) -> str:
    """Convert Canvas UTC due time to a local calendar date for Todoist."""
    return due_dt.astimezone(tz).strftime("%Y-%m-%d")


def format_due_datetime(due_dt: datetime) -> str:
    """Convert Canvas due time to UTC RFC3339 for Todoist."""
    return due_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def assign_priority(due_dt: datetime, now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    hours_left = (due_dt - now).total_seconds() / 3600
    if hours_left <= 72:
        return 4
    elif hours_left <= 168:
        return 3
    elif hours_left <= 336:
        return 2
    else:
        return 1
