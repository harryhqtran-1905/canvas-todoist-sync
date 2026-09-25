from dataclasses import replace
from datetime import datetime, timezone

from canvas_todoist_sync.apply import apply_plan
from canvas_todoist_sync.models import OPEN, Assignment, Link
from canvas_todoist_sync.plan import Action, Plan
from fakes import FakeTodoist

DUE = datetime(2026, 9, 28, 6, 59, 59, tzinfo=timezone.utc)
DUE_Z = "2026-09-28T06:59:59Z"
A1 = Assignment(name="HW1", course="Math", due_at=DUE, course_id=1, assignment_id=101)
A2 = Assignment(name="HW2", course="Math", due_at=DUE, course_id=1, assignment_id=102)


def _create(assignment):
    link = Link(
        task_name=assignment.task_name,
        todoist_id=None,
        status=OPEN,
        canvas_due=DUE.isoformat(),
        synced_due=DUE_Z,
        synced_priority=4,
    )
    payload = {"content": assignment.task_name, "due_datetime": DUE_Z, "priority": 4}
    return Action("create", assignment.key, assignment, payload, link, reason="p1")


def test_create_stores_new_task_id_in_course_section():
    todoist = FakeTodoist()
    links, counts = apply_plan(Plan(actions=[_create(A1)]), todoist, "proj", {})
    assert links[A1.key].todoist_id == "t1"
    assert counts == {"created": 1, "updated": 0, "failed": 0}
    assert todoist.created[0]["project_id"] == "proj"
    assert todoist.created[0]["section_id"] == "sec-Math"


def test_section_created_once_per_course():
    todoist = FakeTodoist()
    sections = {}
    apply_plan(Plan(actions=[_create(A1), _create(A2)]), todoist, "proj", sections)
    assert todoist.sections_created == ["Math"]
    assert sections == {"Math": "sec-Math"}


def test_no_project_means_no_section():
    todoist = FakeTodoist()
    apply_plan(Plan(actions=[_create(A1)]), todoist, None, {})
    assert "section_id" not in todoist.created[0]
    assert todoist.sections_created == []


def test_update_stores_new_link():
    todoist = FakeTodoist()
    todoist.add("t1", A1.task_name, DUE_Z, 4)
    old = Link(task_name=A1.task_name, todoist_id="t1", status=OPEN, canvas_due="old")
    new = replace(old, canvas_due="new")
    action = Action("update", A1.key, A1, {"priority": 3}, new, todoist_id="t1")
    links, counts = apply_plan(Plan(actions=[action], links={A1.key: old}), todoist, "proj", {})
    assert links[A1.key] == new
    assert counts["updated"] == 1
    assert todoist.tasks["t1"][0].priority == 3


def test_failed_update_keeps_previous_link():
    todoist = FakeTodoist()
    todoist.fail_writes = True
    old = Link(task_name=A1.task_name, todoist_id="t1", status=OPEN, canvas_due="old")
    action = Action("update", A1.key, A1, {"priority": 3}, replace(old, canvas_due="new"), todoist_id="t1")
    links, counts = apply_plan(Plan(actions=[action], links={A1.key: old}), todoist, "proj", {})
    assert links[A1.key] == old
    assert counts["failed"] == 1


def test_failed_create_stores_no_link():
    todoist = FakeTodoist()
    todoist.fail_writes = True
    links, counts = apply_plan(Plan(actions=[_create(A1)]), todoist, "proj", {"Math": "sec-Math"})
    assert A1.key not in links
    assert counts == {"created": 0, "updated": 0, "failed": 1}
