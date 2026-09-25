# ID-Based Canvas → Todoist Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop tasks you completed in Todoist from coming back on the next sync. To do that, link each Canvas assignment to its Todoist task by ID and ask Todoist directly what happened to a task, instead of guessing from which names are missing.

**Architecture:** Every run goes through four stages: **fetch → plan → apply → save**. All network reads happen first, and any failure aborts the run before anything is written. `plan.py` is pure: it turns the fetched snapshots into a list of Todoist writes plus updated links. `apply.py` is the only code that writes to Todoist. The state file changes from "lists of task names" to "Canvas assignment key → Link {todoist_id, status, …}", and it is saved atomically.

**Tech Stack:** Python 3.13, `requests` (+ `urllib3.Retry`), `python-dotenv`, `pytest`. Todoist unified API v1, Canvas REST API v1.

---

## Background: the bug being fixed

This was reproduced with mocked APIs and matches `sync_log.txt` on 2026-08-04, where HW7, Quiz 2, Mini-Test 1 and others were recreated 4–5 times.

1. You complete the last open task in the School project, so `get_tasks()` returns `{}`.
2. `classify_vanished_tasks` treats zero tasks as suspicious and marks the vanished tasks as *pending*, so nothing is created this run.
3. `sync.py:332` saves `active_tasks` as only the names that are currently in Todoist, which is `[]`. The pending names are lost.
4. On the next run nothing counts as "vanished" anymore, and the names aren't in `completed_tasks`, so the tasks are **created again**.

The same thing happens whenever the completed-tasks fetch fails (502/503, connection reset, DNS). The underlying cause: identity is a name string, and whether a task is done is **inferred** instead of **asked for**.

## Behavior spec (agreed with the user)

**Todoist is the only place that decides whether something is done.** Canvas submission status is never used, because many assignments (in-class quizzes, paper handins, external tools) never show as submitted there.

| Situation | Result |
|---|---|
| New assignment due within 30 days, no link | Create a task in the course's section, then store a link with the new task ID |
| An unlinked open task with exactly the same name already exists | Link to it (no duplicate). Covers the upgrade from v1 and a lost state file |
| Linked task is missing from the active list | `GET /tasks/{id}`: completed → mark `completed`, deleted/404 → mark `deleted`. **Never recreate** |
| Status lookup fails (network or 5xx) | Abort the whole run and write nothing. The next scheduled run retries |
| Completed/deleted task, Canvas due date changes | Stays completed/deleted. Save the new `canvas_due` and log it |
| You uncheck a completed task in Todoist | It shows up in the active list again, so the link becomes `open` and the **current** Canvas due date is applied |
| Open task, Canvas due date changes | Update the same task ID with the new due date and recalculated priority |
| Open task, you changed its due date, Canvas unchanged | Leave it alone |
| Open task, both you and Canvas changed the due date | Canvas wins, and the log notes that your date was replaced |
| Priority is still the value the sync last set | Follows the deadline, up or down |
| You changed the priority yourself | The sync only raises it, never lowers it |
| Canvas removes the due date | Leave the task alone and keep the link |
| Linked task is open but outside the project | Leave it alone |
| Canvas due date more than 60 days in the past | Drop the link from state |

## File structure

| File | Status | Responsibility |
|---|---|---|
| `canvas_todoist_sync/http_session.py` | **Create** | `requests.Session` with retry/backoff for connection errors and 429/5xx |
| `canvas_todoist_sync/models.py` | Modify | Add `Link`, status constants, `Assignment.key`, `TodoistTask.content` |
| `canvas_todoist_sync/config.py` | Modify | Add `LEGACY_STATE_DAYS`; remove old completed-tracking constants (Task 11) |
| `canvas_todoist_sync/canvas_client.py` | Modify | Use the shared session |
| `canvas_todoist_sync/todoist_client.py` | Modify | Shared session, `get_active_tasks` (by ID), `get_task_status`; drop name-based methods (Task 11) |
| `canvas_todoist_sync/store.py` | **Create** | v2 state: load, v1→v2 conversion, prune, atomic save |
| `canvas_todoist_sync/plan.py` | **Create** | Pure decisions: `select_assignments`, `build_plan`, `Action`, `Plan` |
| `canvas_todoist_sync/apply.py` | **Create** | Execute a plan against Todoist and return the links to save |
| `canvas_todoist_sync/sync.py` | **Rewrite** | `run()` (fetch → plan → apply → save) and `main()` with `--dry-run` |
| `canvas_todoist_sync/state.py` | **Delete** (Task 11) | Replaced by `store.py` |
| `canvas_todoist_sync/sync_logic.py` | Modify (Task 11) | Keep only the date/priority helpers |
| `fakes.py` | **Create** | In-memory `FakeTodoist`, `FakeCanvas`, `FakeResponse` for tests |
| `test_http.py`, `test_models.py`, `test_todoist_client.py`, `test_store.py`, `test_plan.py`, `test_apply.py`, `test_run.py` | **Create** | Tests for each unit |
| `test_sync.py` → `test_sync_logic.py` | Replace (Task 10) | Only the pure date/priority tests remain |
| `README.md`, `.gitignore` | Modify (Task 12) | Document the new behavior and ignore backups and temp files |

Tests stay at the repo root to match the existing `test_sync.py`. Run `pytest` from the repo root. Until Task 10, the old `sync.py` keeps working unchanged, because new code goes into new modules.

---

### Task 1: Retrying HTTP session

**Files:**
- Create: `canvas_todoist_sync/http_session.py`
- Modify: `canvas_todoist_sync/canvas_client.py`, `canvas_todoist_sync/todoist_client.py`, `test_sync.py` (last client test)
- Test: `test_http.py`

- [ ] **Step 1: Write the failing test** — create `test_http.py`:

```python
from canvas_todoist_sync.http_session import RETRY_STATUSES, build_session


def test_session_retries_transient_failures():
    retry = build_session().get_adapter("https://api.todoist.com").max_retries
    assert retry.total == 4
    assert retry.backoff_factor == 2.0
    assert set(RETRY_STATUSES) == {429, 500, 502, 503, 504}
    assert set(retry.status_forcelist) == set(RETRY_STATUSES)
    assert {"GET", "POST"} <= set(retry.allowed_methods)
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `pytest test_http.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'canvas_todoist_sync.http_session'`

- [ ] **Step 3: Implement** — create `canvas_todoist_sync/http_session.py`:

```python
"""Shared HTTP session that retries transient network and server failures."""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

RETRY_STATUSES = (429, 500, 502, 503, 504)


def build_session(total_retries: int = 4, backoff_factor: float = 2.0) -> requests.Session:
    """Session that retries connection errors (e.g. DNS right after wake) and 429/5xx.

    POST is retried too: Todoist de-duplicates writes by X-Request-Id, and a
    retry resends the same headers, so the same id.
    """
    retry = Retry(
        total=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=RETRY_STATUSES,
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session
```

- [ ] **Step 4: Run it and confirm it passes**

Run: `pytest test_http.py -v`
Expected: PASS

- [ ] **Step 5: Use the session in the Canvas client** — replace `canvas_todoist_sync/canvas_client.py` with:

```python
"""Thin Canvas LMS API client. HTTP only — no sync decisions."""

from .config import Settings
from .http_session import build_session


class CanvasClient:
    def __init__(self, settings: Settings, session=None):
        self.base_url = settings.canvas_url
        self.headers = {"Authorization": f"Bearer {settings.canvas_token}"}
        self.session = session or build_session()

    def _get_all(self, url, params=None):
        """GET a Canvas collection, following Link: rel="next" pagination."""
        params = dict(params or {})
        params.setdefault("per_page", 100)
        results = []
        while url:
            r = self.session.get(url, headers=self.headers, params=params)
            r.raise_for_status()
            results.extend(r.json())
            url = r.links.get("next", {}).get("url")
            params = None  # the "next" URL already carries the query string
        return results

    def get_active_courses(self):
        courses = self._get_all(
            f"{self.base_url}/api/v1/courses",
            params={"enrollment_state": "active"},
        )
        return [c for c in courses if "name" in c]

    def get_assignments(self, course_id):
        return self._get_all(
            f"{self.base_url}/api/v1/courses/{course_id}/assignments",
            params={"order_by": "due_at"},
        )
```

- [ ] **Step 6: Use the session in the Todoist client.** In `canvas_todoist_sync/todoist_client.py`:

Replace the imports and `__init__`:

```python
import uuid
from datetime import datetime, timezone

from .config import Settings
from .http_session import build_session
from .models import TodoistTask
from .sync_logic import normalize_todoist_due_date

TODOIST_BASE = "https://api.todoist.com/api/v1"


class TodoistClient:
    def __init__(self, settings: Settings, session=None):
        self.headers = {
            "Authorization": f"Bearer {settings.todoist_token}",
            "Content-Type": "application/json",
        }
        self.session = session or build_session()
```

Then change the three request calls:
- in `_paginate`: `r = requests.get(f"{TODOIST_BASE}/{path}", ...)` → `r = self.session.get(f"{TODOIST_BASE}/{path}", headers=self.headers, params=params)`
- in `_post`: `return requests.post(...)` → `return self.session.post(f"{TODOIST_BASE}/{path}", headers=headers, json=payload)`
- in `get_completed_tasks_by_completion_date`: `r = requests.get(` → `r = self.session.get(`

- [ ] **Step 7: Update the one test that patched `requests.get`.** In `test_sync.py`, add `from types import SimpleNamespace` to the imports. In `test_get_completed_tasks_parses_items_and_paginates`, replace:

```python
    monkeypatch.setattr("canvas_todoist_sync.todoist_client.requests.get", fake_get)
    client = TodoistClient.__new__(TodoistClient)
    client.headers = {}
```

with:

```python
    client = TodoistClient.__new__(TodoistClient)
    client.headers = {}
    client.session = SimpleNamespace(get=fake_get)
```

- [ ] **Step 8: Run the full suite**

Run: `pytest -q`
Expected: `45 passed`

- [ ] **Step 9: Commit**

```bash
git add canvas_todoist_sync/http_session.py canvas_todoist_sync/canvas_client.py canvas_todoist_sync/todoist_client.py test_http.py test_sync.py
git commit -m "feat: retry transient network and 5xx failures with a shared session"
```

---

### Task 2: Verify Todoist's task-status API (manual, one-off)

The design relies on `GET /api/v1/tasks/{id}` for tasks that are no longer active. Check what it returns before building on it. This touches only a throwaway task.

- [ ] **Step 1: Run the probe from the repo root** (it uses your `.env` token, and nothing is saved to disk):

```bash
python - <<'EOF'
import os, requests
from dotenv import load_dotenv
load_dotenv()
H = {"Authorization": f"Bearer {os.environ['TODOIST_API_TOKEN']}"}
B = "https://api.todoist.com/api/v1"
tid = requests.post(f"{B}/tasks", headers=H, json={"content": "probe-canvas-sync (safe to delete)"}).json()["id"]
requests.post(f"{B}/tasks/{tid}/close", headers=H).raise_for_status()
r = requests.get(f"{B}/tasks/{tid}", headers=H)
print("completed ->", r.status_code, {k: r.json().get(k) for k in ("checked", "is_deleted")} if r.ok else r.text[:200])
requests.delete(f"{B}/tasks/{tid}", headers=H).raise_for_status()
r = requests.get(f"{B}/tasks/{tid}", headers=H)
print("deleted   ->", r.status_code, {k: r.json().get(k) for k in ("checked", "is_deleted")} if r.ok else r.text[:200])
EOF
```

Expected:
```
completed -> 200 {'checked': True, 'is_deleted': False}
deleted   -> 404 ...        (or 200 {'checked': ..., 'is_deleted': True})
```

- [ ] **Step 2: Decide**
  - Output matches → continue. `get_task_status` in Task 4 handles all of these shapes.
  - A completed task returns **404** → still fine. `completed` and `deleted` behave the same in the planner (both mean "never recreate"), so only the log label is less precise. Continue.
  - A completed task returns **200 with `checked: False`** → **STOP.** The API can't tell a completed task from an open one, so the design needs rethinking before going further.

No commit for this task.

---

### Task 3: Models — `Link`, status constants, assignment key

**Files:**
- Modify: `canvas_todoist_sync/models.py`
- Test: `test_models.py`

- [ ] **Step 1: Write the failing tests** — create `test_models.py`:

```python
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
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `pytest test_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'OPEN'`

- [ ] **Step 3: Implement** — replace `canvas_todoist_sync/models.py` with:

```python
"""Data models shared across the sync pipeline."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# Link.status values. Only Todoist decides whether a task is done.
OPEN = "open"
COMPLETED = "completed"
DELETED = "deleted"


@dataclass
class Assignment:
    """A Canvas assignment selected for syncing."""

    name: str
    course: str
    due_at: Optional[datetime]
    url: str = ""
    course_id: int = 0
    assignment_id: int = 0

    @property
    def task_name(self) -> str:
        return f"[{self.course}] {self.name}"

    @property
    def key(self) -> str:
        """Stable identity: survives renames of the assignment or course."""
        return f"canvas:{self.course_id}:{self.assignment_id}"


@dataclass
class TodoistTask:
    """The subset of a Todoist task we care about for syncing."""

    id: Optional[str]
    priority: int
    due_date: Optional[str]
    due_datetime: Optional[str]
    content: str = ""


@dataclass
class Link:
    """What the sync knows about one Canvas assignment's Todoist task."""

    task_name: str
    todoist_id: Optional[str]  # None only for names carried over from v1 state
    status: str  # OPEN | COMPLETED | DELETED
    canvas_due: Optional[str]  # Canvas due_at (ISO, UTC) as of the last run
    synced_due: Optional[str] = None  # due_datetime the sync last wrote to Todoist
    synced_priority: Optional[int] = None  # priority the sync last wrote to Todoist
```

- [ ] **Step 4: Run the full suite**

Run: `pytest -q`
Expected: `48 passed`

- [ ] **Step 5: Commit**

```bash
git add canvas_todoist_sync/models.py test_models.py
git commit -m "feat: add Link model and stable Canvas assignment key"
```

---

### Task 4: Todoist client — active tasks by ID, task status lookup; test fakes

**Files:**
- Modify: `canvas_todoist_sync/todoist_client.py`
- Create: `fakes.py`
- Test: `test_todoist_client.py`

- [ ] **Step 1: Create the shared test fakes** — create `fakes.py` at the repo root:

```python
"""In-memory stand-ins for the Canvas and Todoist clients (tests only)."""

import requests

from canvas_todoist_sync.models import COMPLETED, DELETED, OPEN, TodoistTask


class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.text = str(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class FakeTodoist:
    """Tasks live in self.tasks as {id: [TodoistTask, status]}."""

    def __init__(self):
        self.tasks = {}
        self.created = []
        self.updated = []
        self.sections_created = []
        self.fail_status = False
        self.fail_writes = False
        self._next_id = 1

    # --- client API used by the sync ---

    def get_project_id(self, project_name):
        return "proj"

    def get_sections(self, project_id):
        return {}

    def create_section(self, name, project_id):
        self.sections_created.append(name)
        return f"sec-{name}"

    def get_active_tasks(self, project_id=None):
        return {tid: task for tid, (task, status) in self.tasks.items() if status == OPEN}

    def get_task_status(self, task_id):
        if self.fail_status:
            raise requests.ConnectionError("offline")
        return self.tasks[task_id][1] if task_id in self.tasks else DELETED

    def create_task(self, payload):
        if self.fail_writes:
            return FakeResponse(status_code=503)
        task_id = f"t{self._next_id}"
        self._next_id += 1
        self.add(task_id, payload["content"], payload["due_datetime"], payload["priority"])
        self.created.append(payload)
        return FakeResponse({"id": task_id})

    def update_task(self, task_id, payload):
        if self.fail_writes:
            return FakeResponse(status_code=503)
        task = self.tasks[task_id][0]
        if "due_datetime" in payload:
            task.due_datetime = payload["due_datetime"]
            task.due_date = payload["due_datetime"][:10]
        if "priority" in payload:
            task.priority = payload["priority"]
        self.updated.append((task_id, payload))
        return FakeResponse({"id": task_id})

    # --- what the user does in the Todoist app ---

    def add(self, task_id, content, due_datetime, priority):
        task = TodoistTask(
            id=task_id,
            priority=priority,
            due_date=due_datetime[:10],
            due_datetime=due_datetime,
            content=content,
        )
        self.tasks[task_id] = [task, OPEN]

    def complete(self, task_id):
        self.tasks[task_id][1] = COMPLETED

    def uncheck(self, task_id):
        self.tasks[task_id][1] = OPEN

    def delete(self, task_id):
        self.tasks[task_id][1] = DELETED


class FakeCanvas:
    """One course ("Math", id 1) whose raw assignments are self.assignments."""

    def __init__(self, assignments):
        self.assignments = assignments

    def get_active_courses(self):
        return [{"id": 1, "name": "Math"}]

    def get_assignments(self, course_id):
        return self.assignments
```

- [ ] **Step 2: Write the failing tests** — create `test_todoist_client.py`:

```python
import pytest
import requests

from canvas_todoist_sync.config import Settings
from canvas_todoist_sync.models import COMPLETED, DELETED, OPEN
from canvas_todoist_sync.todoist_client import TodoistClient
from fakes import FakeResponse


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, params=None):
        self.calls.append((url, dict(params or {})))
        return self.responses.pop(0)


def _client(*responses):
    settings = Settings(canvas_token="c", canvas_url="https://canvas.test", todoist_token="t")
    return TodoistClient(settings, session=FakeSession(*responses))


@pytest.mark.parametrize(
    "response, expected",
    [
        (FakeResponse({"checked": True, "is_deleted": False}), COMPLETED),
        (FakeResponse({"checked": False, "is_deleted": True}), DELETED),
        (FakeResponse({"checked": False, "is_deleted": False}), OPEN),
        (FakeResponse(status_code=404), DELETED),
    ],
)
def test_get_task_status(response, expected):
    client = _client(response)
    assert client.get_task_status("t1") == expected
    assert client.session.calls[0][0].endswith("/tasks/t1")


def test_get_task_status_raises_on_server_error():
    with pytest.raises(requests.HTTPError):
        _client(FakeResponse(status_code=503)).get_task_status("t1")


def test_get_active_tasks_keyed_by_id_with_content():
    page = {
        "results": [
            {
                "id": "t1",
                "content": "[Math] HW1",
                "priority": 4,
                "due": {"date": "2026-09-27", "datetime": "2026-09-28T06:59:59Z"},
            }
        ],
        "next_cursor": None,
    }
    client = _client(FakeResponse(page))
    tasks = client.get_active_tasks("proj")
    assert set(tasks) == {"t1"}
    assert tasks["t1"].content == "[Math] HW1"
    assert tasks["t1"].due_datetime == "2026-09-28T06:59:59Z"
    assert tasks["t1"].due_date == "2026-09-27"
    assert client.session.calls[0][1]["project_id"] == "proj"
```

- [ ] **Step 3: Run them and confirm they fail**

Run: `pytest test_todoist_client.py -v`
Expected: FAIL with `AttributeError: 'TodoistClient' object has no attribute 'get_task_status'`

- [ ] **Step 4: Implement.** In `canvas_todoist_sync/todoist_client.py`:

Change the models import to `from .models import COMPLETED, DELETED, OPEN, TodoistTask`.

Add this module-level function above `class TodoistClient`:

```python
def _to_task(t) -> TodoistTask:
    due = t.get("due") or {}
    raw_date = due.get("date") or t.get("due_date")
    due_datetime = due.get("datetime")
    # Todoist sometimes stores a full datetime in due.date only.
    if not due_datetime and raw_date and "T" in raw_date:
        due_datetime = raw_date
    return TodoistTask(
        id=t["id"],
        priority=t["priority"],
        due_date=normalize_todoist_due_date(raw_date),
        due_datetime=due_datetime,
        content=t["content"],
    )
```

Replace the body of `get_tasks` so the old code path shares the parser:

```python
    def get_tasks(self, project_id=None):
        """Returns {task_content: TodoistTask}, scoped to a project if given."""
        params = {"project_id": project_id} if project_id else {}
        return {t["content"]: _to_task(t) for t in self._paginate("tasks", params)}
```

Add these two methods after `get_tasks`:

```python
    def get_active_tasks(self, project_id=None):
        """Returns {task_id: TodoistTask} for open tasks, scoped to a project if given."""
        params = {"project_id": project_id} if project_id else {}
        return {t["id"]: _to_task(t) for t in self._paginate("tasks", params)}

    def get_task_status(self, task_id) -> str:
        """OPEN, COMPLETED or DELETED for one task, asked directly by ID.

        Raises on network/server errors so the caller aborts the run
        instead of guessing.
        """
        r = self.session.get(f"{TODOIST_BASE}/tasks/{task_id}", headers=self.headers)
        if r.status_code == 404:
            return DELETED
        r.raise_for_status()
        task = r.json()
        if task.get("is_deleted"):
            return DELETED
        if task.get("checked"):
            return COMPLETED
        return OPEN
```

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: `54 passed`

- [ ] **Step 6: Commit**

```bash
git add canvas_todoist_sync/todoist_client.py fakes.py test_todoist_client.py
git commit -m "feat: fetch Todoist tasks by ID and look up task status directly"
```

---

### Task 5: v2 state store

**Files:**
- Create: `canvas_todoist_sync/store.py`
- Modify: `canvas_todoist_sync/config.py`
- Test: `test_store.py`

- [ ] **Step 1: Add the constant.** In `canvas_todoist_sync/config.py`, below `STALE_TRACKING_DAYS`, add:

```python
LEGACY_STATE_DAYS = 105      # keep v1 task names this long after upgrading state
```

- [ ] **Step 2: Write the failing tests** — create `test_store.py`:

```python
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
```

- [ ] **Step 3: Run them and confirm they fail**

Run: `pytest test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'canvas_todoist_sync.store'`

- [ ] **Step 4: Implement** — create `canvas_todoist_sync/store.py`:

```python
"""Sync state v2: one Link per Canvas assignment, saved atomically.

v1 files (keyed by task name) are converted on load: every name the old
sync knew about becomes a legacy entry, so the first v2 run neither
recreates finished work nor duplicates open tasks.
"""

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .config import LEGACY_STATE_DAYS, STALE_TRACKING_DAYS
from .models import COMPLETED, DELETED, Link

log = logging.getLogger(__name__)

STATE_VERSION = 2


@dataclass
class State:
    links: dict[str, Link] = field(default_factory=dict)
    # Task names from a v1 state file -> COMPLETED | DELETED. Consumed as
    # assignments get linked; dropped entirely once legacy_until passes.
    legacy: dict[str, str] = field(default_factory=dict)
    legacy_until: Optional[str] = None


def load_state(path, now=None) -> State:
    path = Path(path)
    if not path.exists():
        return State()
    data = json.loads(path.read_text())
    if data.get("version") != STATE_VERSION:
        return _from_v1(data, now or datetime.now(timezone.utc))
    return State(
        links={key: Link(**fields) for key, fields in data.get("links", {}).items()},
        legacy=data.get("legacy", {}),
        legacy_until=data.get("legacy_until"),
    )


def _from_v1(data, now) -> State:
    legacy = {}
    # A v1 "active" task that is still open in Todoist gets linked by name
    # before legacy is consulted; if it is gone, it was completed or deleted.
    for name in data.get("active_tasks", []):
        legacy[name] = COMPLETED
    for name in data.get("completed_tasks", {}):
        legacy[name] = COMPLETED
    for name in data.get("removed_tasks", {}):
        legacy[name] = DELETED
    log.info(f"  Upgraded sync state to v{STATE_VERSION} ({len(legacy)} task name(s) carried over).")
    until = (now + timedelta(days=LEGACY_STATE_DAYS)).isoformat()
    return State(legacy=legacy, legacy_until=until)


def prune_links(links, now) -> dict[str, Link]:
    """Drop links whose Canvas due date is long past (dropped courses, etc.)."""
    cutoff = now - timedelta(days=STALE_TRACKING_DAYS)
    kept = {
        key: link
        for key, link in links.items()
        if link.canvas_due is None or datetime.fromisoformat(link.canvas_due) >= cutoff
    }
    if len(kept) < len(links):
        log.info(f"  Pruned {len(links) - len(kept)} stale link(s) from state.")
    return kept


def save_state(path, state: State, now=None) -> None:
    """Write the state atomically: a crash mid-write leaves the old file intact."""
    now = now or datetime.now(timezone.utc)
    legacy, legacy_until = state.legacy, state.legacy_until
    if legacy_until and datetime.fromisoformat(legacy_until) <= now:
        legacy, legacy_until = {}, None
    payload = {
        "version": STATE_VERSION,
        "links": {key: asdict(link) for key, link in sorted(state.links.items())},
        "legacy": legacy,
        "legacy_until": legacy_until,
    }
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
```

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: `61 passed`

- [ ] **Step 6: Commit**

```bash
git add canvas_todoist_sync/store.py canvas_todoist_sync/config.py test_store.py
git commit -m "feat: v2 sync state keyed by Canvas assignment with atomic saves"
```

---

### Task 6: Planner part 1 — select assignments by key

**Files:**
- Create: `canvas_todoist_sync/plan.py`
- Test: `test_plan.py`

- [ ] **Step 1: Write the failing tests** — create `test_plan.py`:

```python
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
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `pytest test_plan.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'canvas_todoist_sync.plan'`

- [ ] **Step 3: Implement** — create `canvas_todoist_sync/plan.py`:

```python
"""Pure sync planning: decide what to do for each assignment. No I/O.

build_plan() takes snapshots fetched beforehand and returns the Todoist
writes to make plus the links to save. Todoist is the only source of truth
for "done": a task completed or deleted there is never recreated or
reopened by the sync.
"""

from datetime import datetime

from .models import Assignment


def select_assignments(raw_assignments, course_id, course_name, now, cutoff, linked_keys):
    """Assignments to consider this run: due within the window, or already linked.

    Linked assignments whose Canvas due date was removed are kept with
    due_at=None so the link is not dropped.
    """
    selected = []
    for raw in raw_assignments:
        candidate = Assignment(
            name=raw["name"],
            course=course_name,
            due_at=None,
            url=raw.get("html_url", ""),
            course_id=course_id,
            assignment_id=raw["id"],
        )
        is_linked = candidate.key in linked_keys
        due_str = raw.get("due_at")
        if not due_str:
            if is_linked:
                selected.append(candidate)
            continue
        due_dt = datetime.fromisoformat(due_str.replace("Z", "+00:00"))
        if now <= due_dt <= cutoff or is_linked:
            candidate.due_at = due_dt
            selected.append(candidate)
    return selected
```

- [ ] **Step 4: Run them and confirm they pass**

Run: `pytest test_plan.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add canvas_todoist_sync/plan.py test_plan.py
git commit -m "feat: select Canvas assignments by stable key"
```

---

### Task 7: Planner part 2 — create, link, and status decisions

**Files:**
- Modify: `canvas_todoist_sync/plan.py`
- Test: `test_plan.py`

- [ ] **Step 1: Write the failing tests.** In `test_plan.py`, replace the import block at the top with:

```python
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from canvas_todoist_sync.models import COMPLETED, DELETED, OPEN, Assignment, Link, TodoistTask
from canvas_todoist_sync.plan import build_plan, select_assignments
```

Then add at the end of the file:

```python
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
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `pytest test_plan.py -v`
Expected: the new tests FAIL with `ImportError: cannot import name 'build_plan'`

- [ ] **Step 3: Implement.** In `canvas_todoist_sync/plan.py`, replace the import block with:

```python
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Optional

from .models import OPEN, Assignment, Link
from .sync_logic import assign_priority, format_due_date, format_due_datetime

PRIORITY_LABELS = {4: "p1", 3: "p2", 2: "p3", 1: "p4"}


@dataclass
class Action:
    """One Todoist write. `link` is stored only if the write succeeds."""

    kind: str  # "create" | "update"
    key: str
    assignment: Assignment
    payload: dict
    link: Link
    todoist_id: Optional[str] = None  # set for updates
    reason: str = ""


@dataclass
class Plan:
    actions: list[Action] = field(default_factory=list)
    links: dict[str, Link] = field(default_factory=dict)
    legacy: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
```

Then add at the end of the file:

```python
def build_plan(assignments, links, active, statuses, legacy, now, tz) -> Plan:
    """Decide what to do for every selected assignment.

    active:   {todoist_id: TodoistTask} open in the project right now.
    statuses: {todoist_id: OPEN | COMPLETED | DELETED} for linked open tasks
              missing from `active`, asked for by ID (never guessed).
    legacy:   {task_name: status} carried over from a v1 state file.

    Links for assignments not seen this run are kept unchanged.
    """
    plan = Plan(links=dict(links), legacy=dict(legacy))
    linked_ids = {link.todoist_id for link in links.values() if link.todoist_id}
    unlinked_by_name = {
        task.content: task for task_id, task in active.items() if task_id not in linked_ids
    }
    for a in assignments:
        _plan_assignment(a, plan, active, statuses, unlinked_by_name, now, tz)
    return plan


def _plan_assignment(a, plan, active, statuses, unlinked_by_name, now, tz):
    link = plan.links.get(a.key)
    if link is None:
        link = _link_existing(a, plan, unlinked_by_name)
        if link is None:
            _plan_create(a, plan, now, tz)
            return
    if link.todoist_id is None and a.task_name in unlinked_by_name:
        task = unlinked_by_name.pop(a.task_name)
        link = replace(link, todoist_id=task.id, synced_due=task.due_datetime, synced_priority=task.priority)
    plan.links[a.key] = link

    status = _current_status(link, active, statuses)
    new_canvas_due = a.due_at.isoformat() if a.due_at else None

    if status != OPEN:
        if link.status == OPEN:
            plan.notes.append(f"Marked {status} in Todoist, will not recreate: {a.task_name}")
        if a.due_at is not None and new_canvas_due != link.canvas_due:
            plan.notes.append(
                f"Deadline changed on {status} task, left {status}: {a.task_name}"
                f" - now due {format_due_date(a.due_at, tz)}"
            )
        plan.links[a.key] = replace(link, status=status, canvas_due=new_canvas_due)
        return

    task = active.get(link.todoist_id)
    if task is None:
        # Open in Todoist but outside the project: leave it be, and keep the
        # old canvas_due so a pending deadline change applies if it comes back.
        plan.notes.append(f"Open outside the project, left alone: {a.task_name}")
        plan.links[a.key] = replace(link, status=OPEN)
        return
    if link.status != OPEN:
        plan.notes.append(f"Reopened in Todoist: {a.task_name}")
    if a.due_at is None:
        plan.notes.append(f"Due date removed in Canvas, task left alone: {a.task_name}")
        plan.links[a.key] = replace(link, status=OPEN, canvas_due=None)
        return
    plan.links[a.key] = replace(link, status=OPEN, canvas_due=new_canvas_due)


def _link_existing(a, plan, unlinked_by_name) -> Optional[Link]:
    """Link an assignment that has no link yet to whatever already exists."""
    task = unlinked_by_name.pop(a.task_name, None)
    if task is not None:
        plan.legacy.pop(a.task_name, None)
        plan.notes.append(f"Linked existing Todoist task: {a.task_name}")
        # canvas_due=None makes the first run re-check the due date against Canvas.
        return Link(
            task_name=a.task_name,
            todoist_id=task.id,
            status=OPEN,
            canvas_due=None,
            synced_due=task.due_datetime,
            synced_priority=task.priority,
        )
    legacy_status = plan.legacy.pop(a.task_name, None)
    if legacy_status is not None:
        plan.notes.append(f"Carried over from old state ({legacy_status}): {a.task_name}")
        return Link(
            task_name=a.task_name,
            todoist_id=None,
            status=legacy_status,
            canvas_due=a.due_at.isoformat() if a.due_at else None,
        )
    return None


def _current_status(link, active, statuses) -> str:
    if link.todoist_id is None:
        return link.status
    if link.todoist_id in active:
        return OPEN  # includes a completed task you unchecked
    if link.status == OPEN:
        return statuses[link.todoist_id]
    return link.status


def _plan_create(a, plan, now, tz):
    due = format_due_datetime(a.due_at)
    priority = assign_priority(a.due_at, now)
    payload = {"content": a.task_name, "due_datetime": due, "priority": priority}
    if a.url:
        payload["description"] = a.url
    link = Link(
        task_name=a.task_name,
        todoist_id=None,
        status=OPEN,
        canvas_due=a.due_at.isoformat(),
        synced_due=due,
        synced_priority=priority,
    )
    reason = f"{PRIORITY_LABELS[priority]}, due {format_due_date(a.due_at, tz)}"
    plan.actions.append(Action("create", a.key, a, payload, link, reason=reason))
```

- [ ] **Step 4: Run them and confirm they pass**

Run: `pytest test_plan.py -v`
Expected: 14 PASS

- [ ] **Step 5: Commit**

```bash
git add canvas_todoist_sync/plan.py test_plan.py
git commit -m "feat: plan creates, links, and Todoist-owned completion status"
```

---

### Task 8: Planner part 3 — due date and priority updates for open tasks

**Files:**
- Modify: `canvas_todoist_sync/plan.py`
- Test: `test_plan.py`

- [ ] **Step 1: Write the failing tests** — add at the end of `test_plan.py`:

```python
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
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `pytest test_plan.py -v`
Expected: the 7 new tests FAIL (for example, `ValueError: not enough values to unpack (expected 1, got 0)` because no update action is produced yet)

- [ ] **Step 3: Implement.** In `canvas_todoist_sync/plan.py`:

Change the `sync_logic` import to:

```python
from .sync_logic import assign_priority, format_due_date, format_due_datetime, parse_due_datetime
```

At the end of `_plan_assignment`, replace this line:

```python
    plan.links[a.key] = replace(link, status=OPEN, canvas_due=new_canvas_due)
```

with:

```python
    reopened = link.status != OPEN
    push_due = reopened or new_canvas_due != link.canvas_due
    _plan_update(a, link, task, push_due, plan, now)
```

Add these functions at the end of the file:

```python
def _plan_update(a, link, task, push_due, plan, now):
    """Bring an open, linked task in line with Canvas without undoing your edits.

    Due date: pushed only when Canvas changed it (or you just reopened the
    task); a date you set yourself is otherwise left alone.
    Priority: follows the deadline while it is still the value the sync set;
    once you change it, the sync only ever raises it.
    """
    desired_due = format_due_datetime(a.due_at)
    desired_priority = assign_priority(a.due_at, now)
    payload, reasons = {}, []

    if push_due and not _same_instant(task.due_datetime, desired_due):
        payload["due_datetime"] = desired_due
        reasons.append("deadline changed")
        if not _same_instant(task.due_datetime, link.synced_due):
            plan.notes.append(f"Canvas deadline change replaced your Todoist due date: {a.task_name}")

    if task.priority == link.synced_priority:
        if desired_priority != task.priority:
            payload["priority"] = desired_priority
    elif desired_priority > task.priority:
        payload["priority"] = desired_priority
    if "priority" in payload:
        reasons.append(f"priority {PRIORITY_LABELS[desired_priority]}")

    new_link = replace(
        link,
        status=OPEN,
        canvas_due=a.due_at.isoformat(),
        synced_due=desired_due if push_due else link.synced_due,
        synced_priority=payload.get("priority", link.synced_priority),
    )
    if payload:
        plan.actions.append(
            Action("update", a.key, a, payload, new_link, todoist_id=task.id, reason=", ".join(reasons))
        )
    else:
        plan.links[a.key] = new_link


def _same_instant(a, b) -> bool:
    """True if two Todoist/Canvas due strings are the same moment."""
    pa, pb = parse_due_datetime(a), parse_due_datetime(b)
    if pa is None or pb is None:
        return a == b
    return pa == pb
```

- [ ] **Step 4: Run them and confirm they pass**

Run: `pytest test_plan.py -v`
Expected: 21 PASS

- [ ] **Step 5: Commit**

```bash
git add canvas_todoist_sync/plan.py test_plan.py
git commit -m "feat: plan due date and priority updates that respect manual edits"
```

---

### Task 9: Apply a plan to Todoist

**Files:**
- Create: `canvas_todoist_sync/apply.py`
- Test: `test_apply.py`

- [ ] **Step 1: Write the failing tests** — create `test_apply.py`:

```python
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
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `pytest test_apply.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'canvas_todoist_sync.apply'`

- [ ] **Step 3: Implement** — create `canvas_todoist_sync/apply.py`:

```python
"""Execute a Plan against Todoist. The only module that writes to Todoist."""

import logging
from dataclasses import replace

import requests

from .models import Link
from .plan import Plan

log = logging.getLogger(__name__)


def apply_plan(plan: Plan, todoist, project_id, sections) -> tuple[dict[str, Link], dict[str, int]]:
    """Run each action and return (links to save, counts).

    An action's link is stored only if its API call succeeded; a failure
    keeps the previous link so the next run retries. `sections` maps
    section name -> id and gains any section created here.
    """
    links = dict(plan.links)
    counts = {"created": 0, "updated": 0, "failed": 0}
    for action in plan.actions:
        name = action.assignment.task_name
        try:
            if action.kind == "create":
                payload = dict(action.payload)
                if project_id:
                    payload["project_id"] = project_id
                    payload["section_id"] = _section_id(
                        todoist, action.assignment.course, project_id, sections
                    )
                r = todoist.create_task(payload)
                r.raise_for_status()
                links[action.key] = replace(action.link, todoist_id=r.json()["id"])
                counts["created"] += 1
                log.info(f"    Created ({action.reason}): {name}")
            else:
                r = todoist.update_task(action.todoist_id, action.payload)
                r.raise_for_status()
                links[action.key] = action.link
                counts["updated"] += 1
                log.info(f"    Updated ({action.reason}): {name}")
        except requests.RequestException as exc:
            counts["failed"] += 1
            log.warning(f"    Failed to {action.kind}: {name} - {exc}")
    return links, counts


def _section_id(todoist, name, project_id, sections):
    if name not in sections:
        sections[name] = todoist.create_section(name, project_id)
        log.info(f"  Created new section: {name}")
    return sections[name]
```

- [ ] **Step 4: Run them and confirm they pass**

Run: `pytest test_apply.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add canvas_todoist_sync/apply.py test_apply.py
git commit -m "feat: apply planned Todoist writes, keeping old links on failure"
```

---

### Task 10: Switch orchestration to fetch → plan → apply → save

**Files:**
- Rewrite: `canvas_todoist_sync/sync.py`
- Delete: `test_sync.py`
- Create: `test_sync_logic.py`, `test_run.py`

- [ ] **Step 1: Write the failing end-to-end tests** — create `test_run.py`:

```python
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
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `pytest test_run.py -v`
Expected: FAIL with `ImportError: cannot import name 'run' from 'canvas_todoist_sync.sync'`

- [ ] **Step 3: Implement** — replace `canvas_todoist_sync/sync.py` with:

```python
"""Orchestration: fetch everything, plan, apply, save.

Any fetch failure aborts the run before anything is written, so a flaky
network can never turn into duplicate or resurrected tasks.
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

import requests

from .apply import apply_plan
from .canvas_client import CanvasClient
from .config import Settings
from .models import OPEN
from .plan import build_plan, select_assignments
from .store import State, load_state, prune_links, save_state
from .todoist_client import TodoistClient

log = logging.getLogger(__name__)


def fetch_statuses(todoist, links, active):
    """Ask Todoist directly about linked open tasks that are no longer active."""
    return {
        link.todoist_id: todoist.get_task_status(link.todoist_id)
        for link in links.values()
        if link.status == OPEN and link.todoist_id and link.todoist_id not in active
    }


def fetch_assignments(canvas, now, cutoff, linked_keys):
    assignments = []
    for course in canvas.get_active_courses():
        try:
            raw = canvas.get_assignments(course["id"])
        except requests.RequestException as exc:
            log.warning(f"  Could not fetch assignments for {course['name']} (left as is): {exc}")
            continue
        assignments.extend(
            select_assignments(raw, course["id"], course["name"], now, cutoff, linked_keys)
        )
    return assignments


def run(settings: Settings, canvas, todoist, dry_run=False, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = now + timedelta(days=settings.days_ahead)

    # 1. Fetch. Any exception here aborts the run before anything is written.
    project_id = todoist.get_project_id(settings.project_name)
    if not project_id:
        log.warning(f"  WARNING: Project '{settings.project_name}' not found - sections will be skipped.")
    sections = todoist.get_sections(project_id) if project_id else {}
    active = todoist.get_active_tasks(project_id)
    state = load_state(settings.state_file, now=now)
    links = prune_links(state.links, now)
    statuses = fetch_statuses(todoist, links, active)
    assignments = fetch_assignments(canvas, now, cutoff, set(links))

    # 2. Plan (pure).
    plan = build_plan(assignments, links, active, statuses, state.legacy, now, settings.timezone)
    for note in plan.notes:
        log.info(f"  {note}")

    if dry_run:
        for action in plan.actions:
            log.info(f"  [dry run] would {action.kind} ({action.reason}): {action.assignment.task_name}")
        log.info(f"\n  Dry run: {len(plan.actions)} change(s) planned, nothing written.")
        return

    # 3. Apply, then 4. save.
    new_links, counts = apply_plan(plan, todoist, project_id, sections)
    save_state(
        settings.state_file,
        State(links=new_links, legacy=plan.legacy, legacy_until=state.legacy_until),
        now=now,
    )

    log.info(f"\n{'-' * 55}")
    log.info(
        f"  Done: {counts['created']} created, {counts['updated']} updated, {counts['failed']} failed"
    )
    log.info(f"{'-' * 55}\n")


def main(argv=None, settings: Settings | None = None):
    parser = argparse.ArgumentParser(
        prog="canvas_todoist_sync", description="Sync Canvas assignments into Todoist."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what would change without writing to Todoist or the state file",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    settings = settings or Settings.from_env()

    log.info(f"\n{'=' * 55}")
    suffix = "  (dry run)" if args.dry_run else ""
    log.info(f"  Canvas to Todoist Sync  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}{suffix}")
    log.info(f"  Window: next {settings.days_ahead} days")
    log.info(f"{'=' * 55}\n")

    try:
        run(settings, CanvasClient(settings), TodoistClient(settings), dry_run=args.dry_run)
    except requests.RequestException as exc:
        log.error(f"  Aborted before making changes (will retry next run): {exc}")
        sys.exit(1)
```

- [ ] **Step 4: Replace the old test file.** The old orchestration tests (`classify_vanished_tasks`, `sync_assignment`, v1 `load_state`, …) cover code that no longer exists. Keep only the pure helper tests.

```bash
git rm test_sync.py
```

Create `test_sync_logic.py`:

```python
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
```

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: all pass (`test_run.py` 8, `test_sync_logic.py` 12, plus earlier files), 0 failures

- [ ] **Step 6: Check the CLI flag is wired**

Run: `python -m canvas_todoist_sync --help`
Expected: usage text listing `--dry-run`

- [ ] **Step 7: Commit**

```bash
git add canvas_todoist_sync/sync.py test_run.py test_sync_logic.py
git commit -m "feat: fetch-plan-apply-save sync that never recreates finished tasks

Fixes completed tasks reappearing when the project emptied or the
completed-tasks fetch failed: completion is now looked up by task ID and
any fetch failure aborts the run before writing."
```

---

### Task 11: Remove dead v1 code

**Files:**
- Delete: `canvas_todoist_sync/state.py`
- Modify: `canvas_todoist_sync/sync_logic.py`, `canvas_todoist_sync/todoist_client.py`, `canvas_todoist_sync/config.py`

- [ ] **Step 1: Delete the v1 state module**

```bash
git rm canvas_todoist_sync/state.py
```

- [ ] **Step 2: Trim `sync_logic.py`** — replace `canvas_todoist_sync/sync_logic.py` with:

```python
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
```

- [ ] **Step 3: Trim `todoist_client.py`.** Delete the methods `get_tasks`, `_format_rfc3339` and `get_completed_tasks_by_completion_date`. Change the top imports to:

```python
import uuid

from .config import Settings
from .http_session import build_session
from .models import COMPLETED, DELETED, OPEN, TodoistTask
from .sync_logic import normalize_todoist_due_date
```

- [ ] **Step 4: Trim `config.py`.** Delete these two lines:

```python
COMPLETED_EXPIRY_DAYS = 105  # ~3.5 months
COMPLETED_LOOKBACK_DAYS = 84  # completed-tasks API window (must stay under ~90 days)
```

- [ ] **Step 5: Confirm nothing still references removed names**

Run: `grep -rnE "COMPLETED_EXPIRY_DAYS|COMPLETED_LOOKBACK_DAYS|is_canvas_task_name|todoist_due_matches|filter_assignments|get_completed_tasks|get_tasks\(|from \.state|import state" canvas_todoist_sync *.py`
Expected: no output

- [ ] **Step 6: Run the full suite**

Run: `pytest -q`
Expected: all pass, 0 failures

- [ ] **Step 7: Commit**

```bash
git add -A canvas_todoist_sync
git commit -m "refactor: remove name-based v1 state and completion inference"
```

---

### Task 12: Docs, ignore rules, and the first real run

**Files:**
- Modify: `README.md`, `.gitignore`

- [ ] **Step 1: Ignore backups and temp files.** In `.gitignore`, under `# Local sync state and logs (machine-specific)`, add:

```
sync_state*.json
.sync_state.json.*.tmp
```

- [ ] **Step 2: Update the README.** In `README.md`, replace the intro paragraph (the one starting "Pulls Canvas assignments…") with:

```markdown
Pulls Canvas assignments due in the next 30 days and creates Todoist tasks,
organized into sections named after each course.

- Each assignment is linked to its Todoist task by ID, so renames don't break tracking.
- **Todoist decides what's done.** A task you complete or delete in Todoist
  is never recreated or reopened. Uncheck it in Todoist to bring it back,
  and it picks up the current Canvas due date.
- When Canvas changes a deadline, the open task is updated in place. Due dates
  you set yourself are left alone unless Canvas changes the deadline again.
- Priority rises as the deadline approaches. If you change it yourself, the sync
  will only ever raise it.
- If Canvas or Todoist can't be reached, the run stops without changing
  anything and the next scheduled run tries again.
```

In the `## Run` section, after the first code block, add:

````markdown
Preview what a run would do without touching Todoist or the state file:

```bash
python -m canvas_todoist_sync --dry-run
```
````

Replace the `## Layout` list with:

```markdown
- `canvas_todoist_sync/config.py` — `Settings` loaded from env, shared constants
- `canvas_todoist_sync/models.py` — `Assignment`, `TodoistTask`, `Link` dataclasses
- `canvas_todoist_sync/http_session.py` — shared session with retry/backoff
- `canvas_todoist_sync/canvas_client.py` — Canvas API (HTTP only)
- `canvas_todoist_sync/todoist_client.py` — Todoist API (HTTP + pagination, task status by ID)
- `canvas_todoist_sync/store.py` — `sync_state.json` v2: links by Canvas assignment, atomic save, v1 upgrade
- `canvas_todoist_sync/plan.py` — pure decisions: what to create/update/leave alone
- `canvas_todoist_sync/apply.py` — executes the plan against Todoist
- `canvas_todoist_sync/sync_logic.py` — pure date and priority helpers
- `canvas_todoist_sync/sync.py` — orchestration: fetch → plan → apply → save (`main`, `--dry-run`)
- `sync.py` — backward-compatible entry-point shim
```

- [ ] **Step 3: Commit the docs**

```bash
git add README.md .gitignore
git commit -m "docs: describe ID-based sync, dry run, and new layout"
```

- [ ] **Step 4: Back up the v1 state** (the first real run converts it in place)

Run: `cp sync_state.json sync_state.v1.backup.json`

- [ ] **Step 5: Dry run against the real accounts**

Run: `python -m canvas_todoist_sync --dry-run`
Expected:
- `Upgraded sync state to v2 (N task name(s) carried over).`
- `Linked existing Todoist task: …` for every Canvas task currently open in Todoist
- **No** `would create` line for anything you've already completed or deleted
- `Dry run: … nothing written.`, and `sync_state.json` unchanged (`git diff --no-index sync_state.v1.backup.json sync_state.json` shows nothing)

If any "would create" line is for something you already finished, stop and investigate before the real run.

- [ ] **Step 6: First real run**

Run: `./run_sync.sh && tail -40 sync_log.txt`
Expected: a `Done: X created, Y updated, 0 failed` summary, and `sync_state.json` now starts with `"version": 2`.

- [ ] **Step 7: Live check of the original bug**
  1. In Todoist, complete one Canvas task (ideally the last open one in the project, which is the exact case that used to break).
  2. Run `./run_sync.sh` twice.
  3. Expected in `sync_log.txt`: `Marked completed in Todoist, will not recreate: …` on the first run, and the task is **not** recreated on either run.
  4. Uncheck it in Todoist and run again. Expected: `Reopened in Todoist: …`, and the task keeps its original ID.

- [ ] **Step 8: Delete the backup once you're satisfied** (keep it for a week or two if unsure)

Run: `rm sync_state.v1.backup.json`
