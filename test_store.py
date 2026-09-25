import json
from datetime import datetime, timedelta, timezone

from canvas_todoist_sync.config import LEGACY_STATE_DAYS, STALE_TRACKING_DAYS
from canvas_todoist_sync.models import COMPLETED, DELETED, OPEN, Link
from canvas_todoist_sync.store import State, load_state, prune_links, save_state

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def _link(canvas_due="2026-09-28T06:59:59+00:00"):
    return Link(
        task_name="[Math] HW1",
        todoist_id="t1",
        status=OPEN,
        canvas_due=canvas_due,
        synced_due="2026-09-28T06:59:59Z",
        synced_priority=4,
    )


def test_missing_file_is_empty_state(tmp_path):
    assert load_state(tmp_path / "nope.json") == State()


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "sync_state.json"
    state = State(links={"canvas:1:101": _link()})
    save_state(path, state, now=NOW)
    assert load_state(path) == state
    assert json.loads(path.read_text())["version"] == 2


def test_save_leaves_no_temp_files(tmp_path):
    path = tmp_path / "sync_state.json"
    save_state(path, State(), now=NOW)
    assert [p.name for p in tmp_path.iterdir()] == ["sync_state.json"]


def test_v1_state_becomes_legacy_names(tmp_path):
    path = tmp_path / "sync_state.json"
    path.write_text(json.dumps({
        "active_tasks": ["[Math] HW1"],
        "completed_tasks": {"[Math] HW2": "2026-09-20T00:00:00+00:00"},
        "removed_tasks": {"[Math] HW3": "2026-09-20T00:00:00+00:00"},
        "canvas_tasks": {"[Math] HW1": "2026-09-28T06:59:59+00:00"},
    }))
    state = load_state(path, now=NOW)
    assert state.links == {}
    assert state.legacy == {
        "[Math] HW1": COMPLETED,
        "[Math] HW2": COMPLETED,
        "[Math] HW3": DELETED,
    }
    assert state.legacy_until == (NOW + timedelta(days=LEGACY_STATE_DAYS)).isoformat()


def test_v1_list_format_completed_tasks(tmp_path):
    path = tmp_path / "sync_state.json"
    path.write_text(json.dumps({"completed_tasks": ["[Math] HW2"]}))
    assert load_state(path, now=NOW).legacy == {"[Math] HW2": COMPLETED}


def test_legacy_dropped_once_expired(tmp_path):
    path = tmp_path / "sync_state.json"
    state = State(
        legacy={"[Math] HW1": COMPLETED},
        legacy_until=(NOW - timedelta(days=1)).isoformat(),
    )
    save_state(path, state, now=NOW)
    loaded = load_state(path)
    assert loaded.legacy == {}
    assert loaded.legacy_until is None


def test_prune_links_drops_only_long_past_due():
    stale = (NOW - timedelta(days=STALE_TRACKING_DAYS + 1)).isoformat()
    recent = (NOW - timedelta(days=1)).isoformat()
    links = {"a": _link(stale), "b": _link(recent), "c": _link(None)}
    assert set(prune_links(links, NOW)) == {"b", "c"}
