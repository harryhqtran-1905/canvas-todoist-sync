"""Unit tests for pure date/priority helpers (no network)."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from canvas_todoist_sync import sync_logic
from canvas_todoist_sync.models import Assignment

LOCAL_TZ = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


# --- normalize_todoist_due_date ---

def test_normalize_date_only():
    assert sync_logic.normalize_todoist_due_date("2026-07-10") == "2026-07-10"


def test_normalize_datetime_string():
    assert sync_logic.normalize_todoist_due_date("2026-07-10T06:59:59Z") == "2026-07-10"


def test_normalize_none():
    assert sync_logic.normalize_todoist_due_date(None) is None


# --- parse_due_datetime ---

def test_parse_z_suffix():
    dt = sync_logic.parse_due_datetime("2026-07-10T06:59:59Z")
    assert dt == datetime(2026, 7, 10, 6, 59, 59, tzinfo=timezone.utc)


def test_parse_offset():
    dt = sync_logic.parse_due_datetime("2026-07-10T06:59:59+00:00")
    assert dt == datetime(2026, 7, 10, 6, 59, 59, tzinfo=timezone.utc)


def test_parse_naive_assumed_utc():
    dt = sync_logic.parse_due_datetime("2026-07-10T06:59:59")
    assert dt == datetime(2026, 7, 10, 6, 59, 59, tzinfo=timezone.utc)


def test_parse_date_only_returns_none():
    assert sync_logic.parse_due_datetime("2026-07-10") is None


def test_parse_empty_returns_none():
    assert sync_logic.parse_due_datetime(None) is None
    assert sync_logic.parse_due_datetime("") is None


# --- format_due_date / format_due_datetime ---

def test_format_due_date_utc_early_morning_is_previous_local_day():
    # 02:00 UTC = 19:00 the previous day in Los Angeles (PDT).
    due = datetime(2026, 7, 10, 2, 0, 0, tzinfo=timezone.utc)
    assert sync_logic.format_due_date(due, LOCAL_TZ) == "2026-07-09"


def test_format_due_datetime_rfc3339_utc():
    due = datetime(2026, 7, 10, 6, 59, 59, tzinfo=timezone.utc)
    assert sync_logic.format_due_datetime(due) == "2026-07-10T06:59:59Z"


# --- assign_priority ---

def test_assign_priority_boundaries():
    assert sync_logic.assign_priority(NOW + timedelta(hours=72), NOW) == 4
    assert sync_logic.assign_priority(NOW + timedelta(hours=73), NOW) == 3
    assert sync_logic.assign_priority(NOW + timedelta(hours=168), NOW) == 3
    assert sync_logic.assign_priority(NOW + timedelta(hours=336), NOW) == 2
    assert sync_logic.assign_priority(NOW + timedelta(hours=337), NOW) == 1


# --- Assignment model ---

def test_assignment_task_name():
    a = Assignment(name="HW1", course="Math", due_at=None)
    assert a.task_name == "[Math] HW1"
