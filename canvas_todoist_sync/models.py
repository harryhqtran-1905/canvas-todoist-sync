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
