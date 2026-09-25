"""Pure sync planning: decide what to do for each assignment. No I/O.

build_plan() takes snapshots fetched beforehand and returns the Todoist
writes to make plus the links to save. Todoist is the only source of truth
for "done": a task completed or deleted there is never recreated or
reopened by the sync.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Optional

from .models import OPEN, Assignment, Link
from .sync_logic import assign_priority, format_due_date, format_due_datetime, parse_due_datetime

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
    reopened = link.status != OPEN
    push_due = reopened or new_canvas_due != link.canvas_due
    _plan_update(a, link, task, push_due, plan, now)


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
