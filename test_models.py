from dataclasses import asdict

from canvas_todoist_sync.models import OPEN, Assignment, Link, TodoistTask


def test_assignment_key_uses_canvas_ids():
    a = Assignment(name="HW1", course="Math", due_at=None, course_id=7, assignment_id=42)
    assert a.key == "canvas:7:42"
    assert a.task_name == "[Math] HW1"


def test_link_round_trips_through_dict():
    link = Link(task_name="[Math] HW1", todoist_id="t1", status=OPEN, canvas_due=None)
    assert Link(**asdict(link)) == link
    assert link.synced_due is None and link.synced_priority is None


def test_todoist_task_content_defaults_to_empty():
    task = TodoistTask(id="t1", priority=1, due_date=None, due_datetime=None)
    assert task.content == ""
