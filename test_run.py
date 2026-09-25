"""End-to-end runs of the sync against in-memory fakes."""

import json
from datetime import datetime, timezone

import pytest
import requests

from canvas_todoist_sync.config import Settings
from canvas_todoist_sync.models import COMPLETED, DELETED, OPEN
from canvas_todoist_sync.store import load_state
from canvas_todoist_sync.sync import run
from fakes import FakeCanvas, FakeTodoist

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
KEY = "canvas:1:101"
DUE_Z = "2026-09-28T06:59:59Z"
LATER_Z = "2026-10-02T06:59:59Z"


def _setup(tmp_path):
    settings = Settings(
        canvas_token="c",
        canvas_url="https://canvas.test",
        todoist_token="t",
        state_file=tmp_path / "sync_state.json",
    )
    canvas = FakeCanvas([{"id": 101, "name": "HW1", "due_at": DUE_Z, "html_url": "https://c/hw1"}])
    return settings, canvas, FakeTodoist()


def test_completed_task_is_never_recreated(tmp_path):
    """The original bug: completing the only task emptied the project and it came back."""
    settings, canvas, todoist = _setup(tmp_path)
    run(settings, canvas, todoist, now=NOW)
    assert len(todoist.created) == 1

    todoist.complete("t1")
    run(settings, canvas, todoist, now=NOW)
    run(settings, canvas, todoist, now=NOW)

    assert len(todoist.created) == 1
    assert load_state(settings.state_file).links[KEY].status == COMPLETED


def test_deleted_task_is_never_recreated(tmp_path):
    settings, canvas, todoist = _setup(tmp_path)
    run(settings, canvas, todoist, now=NOW)
    todoist.delete("t1")
    run(settings, canvas, todoist, now=NOW)
    run(settings, canvas, todoist, now=NOW)
    assert len(todoist.created) == 1
    assert load_state(settings.state_file).links[KEY].status == DELETED


def test_status_fetch_failure_aborts_without_writing(tmp_path):
    settings, canvas, todoist = _setup(tmp_path)
    run(settings, canvas, todoist, now=NOW)
    todoist.complete("t1")
    before = settings.state_file.read_text()

    todoist.fail_status = True
    with pytest.raises(requests.ConnectionError):
        run(settings, canvas, todoist, now=NOW)
    assert settings.state_file.read_text() == before

    todoist.fail_status = False
    run(settings, canvas, todoist, now=NOW)
    assert len(todoist.created) == 1


def test_open_task_follows_canvas_deadline_change(tmp_path):
    settings, canvas, todoist = _setup(tmp_path)
    run(settings, canvas, todoist, now=NOW)
    canvas.assignments[0]["due_at"] = LATER_Z
    run(settings, canvas, todoist, now=NOW)
    assert todoist.updated == [("t1", {"due_datetime": LATER_Z, "priority": 3})]
    assert len(todoist.created) == 1


def test_completed_task_stays_completed_until_unchecked(tmp_path):
    settings, canvas, todoist = _setup(tmp_path)
    run(settings, canvas, todoist, now=NOW)
    todoist.complete("t1")
    canvas.assignments[0]["due_at"] = LATER_Z
    run(settings, canvas, todoist, now=NOW)
    assert todoist.updated == []
    assert todoist.tasks["t1"][1] == COMPLETED

    todoist.uncheck("t1")
    run(settings, canvas, todoist, now=NOW)
    assert todoist.updated == [("t1", {"due_datetime": LATER_Z, "priority": 3})]
    assert load_state(settings.state_file).links[KEY].status == OPEN


def test_manual_due_date_edit_is_kept(tmp_path):
    settings, canvas, todoist = _setup(tmp_path)
    run(settings, canvas, todoist, now=NOW)
    todoist.tasks["t1"][0].due_datetime = "2026-09-27T17:00:00Z"
    run(settings, canvas, todoist, now=NOW)
    assert todoist.updated == []


def test_dry_run_writes_nothing(tmp_path):
    settings, canvas, todoist = _setup(tmp_path)
    run(settings, canvas, todoist, dry_run=True, now=NOW)
    assert todoist.created == []
    assert not settings.state_file.exists()


def test_first_run_after_upgrade_uses_old_state(tmp_path):
    settings, canvas, todoist = _setup(tmp_path)
    canvas.assignments.append({"id": 102, "name": "HW2", "due_at": "2026-09-29T06:59:59Z", "html_url": ""})
    todoist.add("old1", "[Math] HW1", DUE_Z, 4)
    settings.state_file.write_text(json.dumps({
        "active_tasks": ["[Math] HW1"],
        "completed_tasks": {"[Math] HW2": "2026-09-20T00:00:00+00:00"},
        "removed_tasks": {},
        "canvas_tasks": {"[Math] HW1": "2026-09-28T06:59:59+00:00"},
    }))

    run(settings, canvas, todoist, now=NOW)

    assert todoist.created == []
    assert todoist.updated == []
    state = load_state(settings.state_file)
    assert state.links["canvas:1:101"].todoist_id == "old1"
    assert state.links["canvas:1:102"].status == COMPLETED
    assert state.legacy == {}
