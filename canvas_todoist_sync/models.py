"""Data models shared across the sync pipeline."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Assignment:
    """A Canvas assignment selected for syncing."""

    name: str
    course: str
    due_at: Optional[datetime]
    url: str = ""

    @property
    def task_name(self) -> str:
        return f"[{self.course}] {self.name}"


@dataclass
class TodoistTask:
    """The subset of a Todoist task we care about for syncing."""

    id: Optional[str]
    priority: int
    due_date: Optional[str]
    due_datetime: Optional[str]
