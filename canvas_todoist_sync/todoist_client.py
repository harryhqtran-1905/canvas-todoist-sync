"""Thin Todoist API client. HTTP and pagination only — no sync decisions."""

import uuid
from datetime import datetime, timezone

import requests

from .config import Settings
from .models import TodoistTask
from .sync_logic import normalize_todoist_due_date

TODOIST_BASE = "https://api.todoist.com/api/v1"


class TodoistClient:
    def __init__(self, settings: Settings):
        self.headers = {
            "Authorization": f"Bearer {settings.todoist_token}",
            "Content-Type": "application/json",
        }

    def _paginate(self, path, params=None):
        """Yield results from a cursor-paginated Todoist collection."""
        params = dict(params or {})
        params.setdefault("limit", 200)
        cursor = None
        while True:
            if cursor:
                params["cursor"] = cursor
            r = requests.get(f"{TODOIST_BASE}/{path}", headers=self.headers, params=params)
            r.raise_for_status()
            data = r.json()
            yield from data.get("results", [])
            cursor = data.get("next_cursor")
            if not cursor:
                return

    def _post(self, path, payload):
        headers = dict(self.headers)
        headers["X-Request-Id"] = str(uuid.uuid4())
        return requests.post(f"{TODOIST_BASE}/{path}", headers=headers, json=payload)

    def get_project_id(self, project_name):
        for p in self._paginate("projects"):
            if p["name"].lower() == project_name.lower():
                return p["id"]
        return None

    def get_sections(self, project_id):
        """Returns a dict of {section_name: section_id} for the given project."""
        return {
            s["name"]: s["id"]
            for s in self._paginate("sections", {"project_id": project_id})
        }

    def create_section(self, name, project_id):
        r = self._post("sections", {"name": name, "project_id": project_id})
        r.raise_for_status()
        return r.json()["id"]

    def get_tasks(self, project_id=None):
        """Returns {task_content: TodoistTask}, scoped to a project if given."""
        params = {"project_id": project_id} if project_id else {}
        tasks = {}
        for t in self._paginate("tasks", params):
            due = t.get("due") or {}
            raw_date = due.get("date") or t.get("due_date")
            due_datetime = due.get("datetime")
            # Todoist sometimes stores a full datetime in due.date only.
            if not due_datetime and raw_date and "T" in raw_date:
                due_datetime = raw_date
            tasks[t["content"]] = TodoistTask(
                id=t["id"],
                priority=t["priority"],
                due_date=normalize_todoist_due_date(raw_date),
                due_datetime=due_datetime,
            )
        return tasks

    def create_task(self, payload):
        return self._post("tasks", payload)

    def update_task(self, task_id, payload):
        return self._post(f"tasks/{task_id}", payload)

    @staticmethod
    def _format_rfc3339(dt: datetime) -> str:
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def get_completed_tasks_by_completion_date(self, project_id, since: datetime, until: datetime):
        """Return task content strings completed in [since, until] for a project."""
        params = {
            "since": self._format_rfc3339(since),
            "until": self._format_rfc3339(until),
            "limit": 200,
        }
        if project_id:
            params["project_id"] = project_id
        names: set[str] = set()
        cursor = None
        while True:
            req_params = dict(params)
            if cursor:
                req_params["cursor"] = cursor
            r = requests.get(
                f"{TODOIST_BASE}/tasks/completed/by_completion_date",
                headers=self.headers,
                params=req_params,
            )
            r.raise_for_status()
            data = r.json()
            for item in data.get("items", []):
                content = item.get("content")
                if content:
                    names.add(content)
            cursor = data.get("next_cursor")
            if not cursor:
                break
        return names
