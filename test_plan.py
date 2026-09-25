"""Tests for plan.py: pure decisions, no network."""

from datetime import datetime, timedelta, timezone

from canvas_todoist_sync.plan import select_assignments

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
CUTOFF = NOW + timedelta(days=30)
KEY = "canvas:1:101"


def _raw(due_at, name="HW1", assignment_id=101):
    return {"id": assignment_id, "name": name, "due_at": due_at, "html_url": f"https://c/{name}"}


# --- select_assignments ---

def test_select_in_window():
    [a] = select_assignments([_raw("2026-09-28T06:59:59Z")], 1, "Math", NOW, CUTOFF, set())
    assert a.key == KEY
    assert a.task_name == "[Math] HW1"
    assert a.due_at == datetime(2026, 9, 28, 6, 59, 59, tzinfo=timezone.utc)
    assert a.url == "https://c/HW1"


def test_select_skips_unlinked_outside_window():
    assert select_assignments([_raw("2026-12-01T00:00:00Z")], 1, "Math", NOW, CUTOFF, set()) == []
    assert select_assignments([_raw("2026-09-01T00:00:00Z")], 1, "Math", NOW, CUTOFF, set()) == []


def test_select_keeps_linked_outside_window():
    [a] = select_assignments([_raw("2026-12-01T00:00:00Z")], 1, "Math", NOW, CUTOFF, {KEY})
    assert a.due_at.month == 12


def test_select_keeps_linked_with_removed_due_date():
    [a] = select_assignments([_raw(None)], 1, "Math", NOW, CUTOFF, {KEY})
    assert a.due_at is None


def test_select_skips_unlinked_without_due_date():
    assert select_assignments([_raw(None)], 1, "Math", NOW, CUTOFF, set()) == []
