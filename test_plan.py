"""Tests for plan.py: pure decisions, no network."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from canvas_todoist_sync.models import COMPLETED, DELETED, OPEN, Assignment, Link, TodoistTask
from canvas_todoist_sync.plan import build_plan, select_assignments

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


# --- build_plan ---

TZ = ZoneInfo("America/Los_Angeles")
DUE = datetime(2026, 9, 28, 6, 59, 59, tzinfo=timezone.utc)    # ~67h away -> p1 (4)
DUE_Z = "2026-09-28T06:59:59Z"
LATER = datetime(2026, 10, 2, 6, 59, 59, tzinfo=timezone.utc)  # ~163h away -> p2 (3)
LATER_Z = "2026-10-02T06:59:59Z"
FAR = datetime(2026, 10, 20, 6, 59, 59, tzinfo=timezone.utc)   # ~25 days away -> p4 (1)
FAR_Z = "2026-10-20T06:59:59Z"


def _a(due=DUE):
    return Assignment(
        name="HW1", course="Math", due_at=due, url="https://c/HW1", course_id=1, assignment_id=101
    )


def _task(tid="t1", due=DUE_Z, priority=4):
    return TodoistTask(id=tid, priority=priority, due_date=due[:10], due_datetime=due, content="[Math] HW1")


def _link(status=OPEN, canvas_due=DUE, synced_due=DUE_Z, synced_priority=4, tid="t1"):
    return Link(
        task_name="[Math] HW1",
        todoist_id=tid,
        status=status,
        canvas_due=canvas_due.isoformat() if canvas_due else None,
        synced_due=synced_due,
        synced_priority=synced_priority,
    )


def _plan(assignments, links=None, active=None, statuses=None, legacy=None):
    return build_plan(assignments, links or {}, active or {}, statuses or {}, legacy or {}, NOW, TZ)


def test_new_assignment_is_created():
    plan = _plan([_a()])
    [action] = plan.actions
    assert action.kind == "create"
    assert action.payload == {
        "content": "[Math] HW1",
        "due_datetime": DUE_Z,
        "priority": 4,
        "description": "https://c/HW1",
    }
    assert action.link.status == OPEN
    assert action.link.synced_due == DUE_Z
    assert action.link.synced_priority == 4
    assert KEY not in plan.links  # stored only once the create succeeds


def test_open_task_with_same_name_is_linked_not_duplicated():
    plan = _plan([_a()], active={"t9": _task("t9")})
    assert plan.actions == []
    assert plan.links[KEY].todoist_id == "t9"
    assert plan.links[KEY].status == OPEN


def test_legacy_completed_name_is_not_recreated():
    plan = _plan([_a()], legacy={"[Math] HW1": COMPLETED})
    assert plan.actions == []
    assert plan.links[KEY].status == COMPLETED
    assert plan.links[KEY].todoist_id is None
    assert plan.legacy == {}


def test_legacy_link_is_attached_to_open_task_with_same_name():
    links = {KEY: _link(status=COMPLETED, tid=None)}
    plan = _plan([_a()], links=links, active={"t9": _task("t9")})
    assert plan.actions == []
    assert plan.links[KEY].todoist_id == "t9"
    assert plan.links[KEY].status == OPEN


def test_completed_in_todoist_is_recorded_and_not_recreated():
    plan = _plan([_a()], links={KEY: _link()}, statuses={"t1": COMPLETED})
    assert plan.actions == []
    assert plan.links[KEY].status == COMPLETED


def test_deleted_in_todoist_is_not_recreated():
    plan = _plan([_a()], links={KEY: _link()}, statuses={"t1": DELETED})
    assert plan.actions == []
    assert plan.links[KEY].status == DELETED


def test_open_outside_project_is_left_alone():
    plan = _plan([_a(LATER)], links={KEY: _link()}, statuses={"t1": OPEN})
    assert plan.actions == []
    assert plan.links[KEY] == _link()  # canvas_due kept so the change applies if it returns


def test_completed_task_stays_completed_when_deadline_moves():
    plan = _plan([_a(LATER)], links={KEY: _link(status=COMPLETED)})
    assert plan.actions == []
    assert plan.links[KEY].status == COMPLETED
    assert plan.links[KEY].canvas_due == LATER.isoformat()
    assert any("Deadline changed" in note for note in plan.notes)


def test_due_date_removed_in_canvas_leaves_task_alone():
    plan = _plan([_a(None)], links={KEY: _link()}, active={"t1": _task()})
    assert plan.actions == []
    assert plan.links[KEY].canvas_due is None
    assert plan.links[KEY].todoist_id == "t1"


# --- updates to open tasks ---

def test_open_task_follows_canvas_deadline_change():
    plan = _plan([_a(LATER)], links={KEY: _link()}, active={"t1": _task()})
    [action] = plan.actions
    assert action.kind == "update"
    assert action.todoist_id == "t1"
    assert action.payload == {"due_datetime": LATER_Z, "priority": 3}
    assert action.link.canvas_due == LATER.isoformat()
    assert action.link.synced_due == LATER_Z
    assert action.link.synced_priority == 3
    assert plan.links[KEY] == _link()  # new link stored only if the update succeeds


def test_unchecked_task_gets_current_canvas_deadline():
    links = {KEY: _link(status=COMPLETED, canvas_due=LATER)}  # change recorded while completed
    plan = _plan([_a(LATER)], links=links, active={"t1": _task()})
    [action] = plan.actions
    assert action.payload == {"due_datetime": LATER_Z, "priority": 3}
    assert action.link.status == OPEN


def test_manual_due_edit_kept_when_canvas_unchanged():
    plan = _plan([_a()], links={KEY: _link()}, active={"t1": _task(due="2026-09-27T17:00:00Z")})
    assert plan.actions == []


def test_canvas_change_overrides_manual_due_edit_with_note():
    plan = _plan([_a(LATER)], links={KEY: _link()}, active={"t1": _task(due="2026-09-27T17:00:00Z")})
    [action] = plan.actions
    assert action.payload["due_datetime"] == LATER_Z
    assert any("replaced your Todoist due date" in note for note in plan.notes)


def test_sync_owned_priority_escalates_as_deadline_nears():
    plan = _plan([_a()], links={KEY: _link(synced_priority=3)}, active={"t1": _task(priority=3)})
    [action] = plan.actions
    assert action.payload == {"priority": 4}


def test_manual_priority_never_lowered():
    links = {KEY: _link(canvas_due=FAR, synced_due=FAR_Z, synced_priority=1)}
    plan = _plan([_a(FAR)], links=links, active={"t1": _task(due=FAR_Z, priority=4)})
    assert plan.actions == []


def test_manual_priority_raised_when_deadline_is_close():
    plan = _plan([_a()], links={KEY: _link(synced_priority=1)}, active={"t1": _task(priority=2)})
    [action] = plan.actions
    assert action.payload == {"priority": 4}
    assert action.link.synced_priority == 4
