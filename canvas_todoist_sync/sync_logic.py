"""Pure sync decision logic: date parsing/formatting, filtering, priorities.

No network or file I/O here — everything is directly unit-testable.
"""

import re
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from .models import Assignment, TodoistTask

CANVAS_TASK_NAME_RE = re.compile(r"^\[.+\] ")


def is_canvas_task_name(name: str) -> bool:
    """Canvas-synced tasks are named '[Course Name] Assignment Name'."""
    return bool(CANVAS_TASK_NAME_RE.match(name))


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


def todoist_due_matches(existing: TodoistTask, due_date: str, due_datetime: str) -> bool:
    existing_dt = parse_due_datetime(existing.due_datetime)
    if existing_dt is not None:
        return existing_dt == parse_due_datetime(due_datetime)
    return existing.due_date == due_date


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


def filter_assignments(raw_assignments, course_name, now, cutoff, tracked_task_names) -> list:
    """Select assignments to sync: due in-window, or already tracked.

    Tracked assignments whose due date was removed in Canvas are kept
    with due_at=None so tracking is not silently dropped.
    """
    assignments = []
    for a in raw_assignments:
        candidate = Assignment(
            name=a["name"],
            course=course_name,
            due_at=None,
            url=a.get("html_url", ""),
        )
        is_tracked = candidate.task_name in tracked_task_names
        due_str = a.get("due_at")
        if not due_str:
            if is_tracked:
                assignments.append(candidate)
            continue
        due_dt = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
        if now <= due_dt <= cutoff or is_tracked:
            candidate.due_at = due_dt
            assignments.append(candidate)
    return assignments
