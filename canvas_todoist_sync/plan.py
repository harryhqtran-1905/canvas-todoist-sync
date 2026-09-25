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
