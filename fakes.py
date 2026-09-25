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
