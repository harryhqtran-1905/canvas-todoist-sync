"""Unit tests for pure sync logic, state handling, and orchestration guards (no network)."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from canvas_todoist_sync import state, sync, sync_logic
from canvas_todoist_sync.config import COMPLETED_LOOKBACK_DAYS, STALE_TRACKING_DAYS
from canvas_todoist_sync.models import Assignment, TodoistTask
from canvas_todoist_sync.todoist_client import TodoistClient

LOCAL_TZ = ZoneInfo("America/Los_Angeles")


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


# --- todoist_due_matches ---

def _task(due_date=None, due_datetime=None):
    return TodoistTask(id="1", priority=1, due_date=due_date, due_datetime=due_datetime)


def test_due_matches_z_vs_offset():
    existing = _task(due_date="2026-07-09", due_datetime="2026-07-10T06:59:59+00:00")
    assert sync_logic.todoist_due_matches(existing, "2026-07-09", "2026-07-10T06:59:59Z")


def test_due_matches_naive_vs_z():
    existing = _task(due_date="2026-07-09", due_datetime="2026-07-10T06:59:59")
    assert sync_logic.todoist_due_matches(existing, "2026-07-09", "2026-07-10T06:59:59Z")


def test_due_mismatch_different_time():
    existing = _task(due_date="2026-07-09", due_datetime="2026-07-10T06:59:59Z")
    assert not sync_logic.todoist_due_matches(existing, "2026-07-09", "2026-07-10T08:00:00Z")


def test_due_date_only_fallback():
    existing = _task(due_date="2026-07-09", due_datetime=None)
    assert sync_logic.todoist_due_matches(existing, "2026-07-09", "2026-07-10T06:59:59Z")
    assert not sync_logic.todoist_due_matches(existing, "2026-07-10", "2026-07-10T06:59:59Z")


# --- format_due_date / format_due_datetime ---

def test_format_due_date_utc_early_morning_is_previous_local_day():
    # 02:00 UTC = 19:00 the previous day in Los Angeles (PDT).
    due = datetime(2026, 7, 10, 2, 0, 0, tzinfo=timezone.utc)
    assert sync_logic.format_due_date(due, LOCAL_TZ) == "2026-07-09"


def test_format_due_datetime_rfc3339_utc():
    due = datetime(2026, 7, 10, 6, 59, 59, tzinfo=timezone.utc)
    assert sync_logic.format_due_datetime(due) == "2026-07-10T06:59:59Z"


# --- is_canvas_task_name ---

def test_is_canvas_task_name():
    assert sync_logic.is_canvas_task_name("[ICS 45C:  Programming in C/C++] Project 1")
    assert not sync_logic.is_canvas_task_name("Laundry")
    assert not sync_logic.is_canvas_task_name("[]no space bracket")


# --- Assignment model ---

def test_assignment_task_name():
    a = Assignment(name="HW1", course="Math", due_at=None)
    assert a.task_name == "[Math] HW1"


# --- filter_assignments ---

NOW = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)
CUTOFF = NOW + timedelta(days=30)


def _assignment(due_at):
    return Assignment(name="HW1", course="Math", due_at=due_at)


def _raw(name, due_at):
    return {"name": name, "due_at": due_at, "html_url": f"https://c/{name}"}


def test_filter_in_window_included():
    result = sync_logic.filter_assignments(
        [_raw("HW1", "2026-07-10T06:59:59Z")], "Math", NOW, CUTOFF, set()
    )
    assert [a.name for a in result] == ["HW1"]
    assert result[0].due_at == datetime(2026, 7, 10, 6, 59, 59, tzinfo=timezone.utc)


def test_filter_out_of_window_untracked_excluded():
    result = sync_logic.filter_assignments(
        [_raw("Final", "2026-12-01T00:00:00Z")], "Math", NOW, CUTOFF, set()
    )
    assert result == []


def test_filter_tracked_outside_window_included():
    tracked = {"[Math] Final"}
    result = sync_logic.filter_assignments(
        [_raw("Final", "2026-12-01T00:00:00Z")], "Math", NOW, CUTOFF, tracked
    )
    assert [a.name for a in result] == ["Final"]


def test_filter_tracked_removed_due_date_kept_with_none():
    tracked = {"[Math] HW1"}
    result = sync_logic.filter_assignments(
        [_raw("HW1", None)], "Math", NOW, CUTOFF, tracked
    )
    assert len(result) == 1
    assert result[0].due_at is None


def test_filter_untracked_no_due_date_excluded():
    result = sync_logic.filter_assignments(
        [_raw("HW1", None)], "Math", NOW, CUTOFF, set()
    )
    assert result == []


# --- prune_canvas_tasks ---

def test_prune_keeps_recent_and_none_drops_stale():
    recent = (NOW - timedelta(days=5)).isoformat()
    stale = (NOW - timedelta(days=STALE_TRACKING_DAYS + 1)).isoformat()
    tasks = {
        "[Math] Recent": recent,
        "[Math] Stale": stale,
        "[Math] NoDue": None,
    }
    kept = state.prune_canvas_tasks(tasks, now=NOW)
    assert set(kept) == {"[Math] Recent", "[Math] NoDue"}


# --- load_state migration ---

def test_load_state_filters_personal_tasks(tmp_path):
    data = {
        "active_tasks": ["Laundry", "[Math] HW1", "TrackedButPlainName"],
        "completed_tasks": {
            "LeetCode": "2026-06-01T00:00:00+00:00",
            "[Math] HW0": "2026-06-01T00:00:00+00:00",
        },
        "canvas_tasks": {"[Math] HW1": "2026-07-10T06:59:59+00:00"},
    }
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(data))

    loaded = state.load_state(state_file)
    assert loaded["active_tasks"] == ["[Math] HW1"]
    assert set(loaded["completed_tasks"]) == {"[Math] HW0"}
    assert loaded["canvas_tasks"] == {"[Math] HW1": "2026-07-10T06:59:59+00:00"}


def test_load_state_missing_file(tmp_path):
    loaded = state.load_state(tmp_path / "nope.json")
    assert loaded == {
        "active_tasks": [],
        "completed_tasks": {},
        "removed_tasks": {},
        "canvas_tasks": {},
    }


# --- classify_vanished_tasks ---

def test_classify_completed_task_gone_from_todoist():
    prev_active = {"[Math] HW1", "[Math] HW2"}
    existing = {"[Math] HW2": _task()}
    completed_names = {"[Math] HW1"}
    result = sync.classify_vanished_tasks(prev_active, existing, completed_names)
    assert result.completed == ["[Math] HW1"]
    assert result.removed == []
    assert result.pending == set()


def test_classify_completed_overdue_task_is_completed():
    prev_active = {"[Math] HW1", "[Math] HW2"}
    existing = {"[Math] HW2": _task()}
    completed_names = {"[Math] HW1"}
    result = sync.classify_vanished_tasks(prev_active, existing, completed_names)
    assert result.completed == ["[Math] HW1"]
    assert result.removed == []
    assert result.pending == set()


def test_classify_vanished_but_not_in_completed_is_removed():
    prev_active = {"[Math] HW1", "[Math] HW2"}
    existing = {"[Math] HW2": _task()}
    result = sync.classify_vanished_tasks(prev_active, existing, set())
    assert result.completed == []
    assert result.removed == ["[Math] HW1"]
    assert result.pending == set()


def test_classify_recurring_task_still_active_not_marked_completed():
    prev_active = {"[Math] HW1"}
    existing = {"[Math] HW1": _task()}
    completed_names = {"[Math] HW1"}
    result = sync.classify_vanished_tasks(prev_active, existing, completed_names)
    assert result == sync.VanishedClassification([], [], set())


def test_classify_skipped_when_fetch_empty():
    prev_active = {"[Math] HW1"}
    result = sync.classify_vanished_tasks(prev_active, {}, set())
    assert result.completed == []
    assert result.removed == []
    assert result.pending == {"[Math] HW1"}


def test_classify_completed_fetch_failed_marks_pending():
    prev_active = {"[Math] HW1", "[Math] HW2"}
    existing = {"[Math] HW2": _task()}
    result = sync.classify_vanished_tasks(prev_active, existing, None)
    assert result.completed == []
    assert result.removed == []
    assert result.pending == {"[Math] HW1"}


# --- reopen_tasks_in_todoist ---

def test_reopened_in_todoist_removed_from_completed():
    existing = {"[Math] HW1": _task()}
    completed = {"[Math] HW1": NOW.isoformat()}
    removed = {}
    sync.reopen_tasks_in_todoist(existing, completed, removed)
    assert "[Math] HW1" not in completed


def test_reopened_in_todoist_removed_from_removed_tasks():
    existing = {"[Math] HW1": _task()}
    completed = {}
    removed = {"[Math] HW1": NOW.isoformat()}
    sync.reopen_tasks_in_todoist(existing, completed, removed)
    assert "[Math] HW1" not in removed


# --- should_skip_removed ---

def test_skip_removed_task():
    removed = {"[Math] HW1": NOW.isoformat()}
    a = _assignment(NOW + timedelta(days=2))
    assert sync.should_skip_removed(a, removed, canvas_due_changed=False, now=NOW)
    assert "[Math] HW1" in removed


def test_reopen_removed_when_deadline_moved_to_future():
    removed = {"[Math] HW1": NOW.isoformat()}
    a = _assignment(NOW + timedelta(days=2))
    assert not sync.should_skip_removed(a, removed, canvas_due_changed=True, now=NOW)
    assert "[Math] HW1" not in removed


# --- should_skip_completed ---


def test_skip_completed_task():
    completed = {"[Math] HW1": NOW.isoformat()}
    a = _assignment(NOW + timedelta(days=2))
    assert sync.should_skip_completed(a, completed, canvas_due_changed=False, now=NOW)
    assert "[Math] HW1" in completed


def test_reopen_when_deadline_moved_to_future():
    completed = {"[Math] HW1": NOW.isoformat()}
    a = _assignment(NOW + timedelta(days=2))
    assert not sync.should_skip_completed(a, completed, canvas_due_changed=True, now=NOW)
    assert "[Math] HW1" not in completed


def test_no_reopen_when_deadline_changed_to_past():
    completed = {"[Math] HW1": NOW.isoformat()}
    a = _assignment(NOW - timedelta(days=1))
    assert sync.should_skip_completed(a, completed, canvas_due_changed=True, now=NOW)
    assert "[Math] HW1" in completed


def test_not_completed_never_skipped():
    a = _assignment(NOW + timedelta(days=2))
    assert not sync.should_skip_completed(a, {}, canvas_due_changed=False, now=NOW)
    assert not sync.should_skip_completed(a, None, canvas_due_changed=False, now=NOW)


# --- sync_assignment ---

class _StubResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.text)


class _StubTodoist:
    def __init__(self):
        self.create_calls = []
        self.update_calls = []

    def create_task(self, payload):
        self.create_calls.append(payload)
        return _StubResponse(payload={"id": "new-task"})

    def update_task(self, task_id, payload):
        self.update_calls.append((task_id, payload))
        return _StubResponse()


class _StubSettings:
    timezone = LOCAL_TZ


def test_sync_assignment_does_not_create_vanished_pending_task():
    client = _StubTodoist()
    assignment = _assignment(NOW + timedelta(days=2))
    result = sync.sync_assignment(
        assignment,
        client,
        _StubSettings(),
        "project",
        "section",
        {},
        completed_tasks={},
        removed_tasks={},
        suppress_create_names={"[Math] HW1"},
    )
    assert result == "skipped"
    assert client.create_calls == []


def test_sync_assignment_does_not_create_removed_task():
    client = _StubTodoist()
    assignment = _assignment(NOW + timedelta(days=2))
    result = sync.sync_assignment(
        assignment,
        client,
        _StubSettings(),
        "project",
        "section",
        {},
        completed_tasks={},
        removed_tasks={"[Math] HW1": NOW.isoformat()},
    )
    assert result == "skipped"
    assert client.create_calls == []


def test_sync_assignment_still_creates_new_overdue_assignment():
    client = _StubTodoist()
    assignment = _assignment(NOW - timedelta(days=2))
    result = sync.sync_assignment(
        assignment,
        client,
        _StubSettings(),
        "project",
        "section",
        {},
        completed_tasks={},
        removed_tasks={},
        suppress_create_names=set(),
    )
    assert result == "created"
    assert len(client.create_calls) == 1


def test_sync_assignment_updates_existing_overdue_task():
    client = _StubTodoist()
    assignment = _assignment(NOW - timedelta(days=1))
    existing = {
        "[Math] HW1": TodoistTask(
            id="task-1",
            priority=1,
            due_date="2026-07-01",
            due_datetime="2026-07-01T12:00:00Z",
        )
    }
    result = sync.sync_assignment(
        assignment,
        client,
        _StubSettings(),
        "project",
        "section",
        existing,
        completed_tasks={},
        removed_tasks={},
        prev_canvas_due=(NOW - timedelta(days=5)).isoformat(),
    )
    assert result == "updated"
    assert client.create_calls == []
    assert client.update_calls


# --- TodoistClient.get_completed_tasks_by_completion_date ---

def test_get_completed_tasks_parses_items_and_paginates(monkeypatch):
    calls = []

    def fake_get(url, headers=None, params=None):
        calls.append(dict(params or {}))
        if "cursor" not in (params or {}):
            return _StubResponse(
                payload={
                    "items": [{"content": "[Math] HW1"}],
                    "next_cursor": "abc",
                }
            )
        return _StubResponse(
            payload={
                "items": [{"content": "[Math] HW2"}],
                "next_cursor": None,
            }
        )

    client = TodoistClient.__new__(TodoistClient)
    client.headers = {}
    client.session = SimpleNamespace(get=fake_get)
    since = NOW - timedelta(days=7)
    names = client.get_completed_tasks_by_completion_date("proj-1", since, NOW)
    assert names == {"[Math] HW1", "[Math] HW2"}
    assert calls[0]["project_id"] == "proj-1"
    assert "since" in calls[0]
    assert "until" in calls[0]
    assert calls[1]["cursor"] == "abc"


def test_completed_lookback_under_api_cap():
    assert COMPLETED_LOOKBACK_DAYS < 90
