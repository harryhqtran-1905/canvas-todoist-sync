"""Orchestration: pulls Canvas assignments due in the next N days and creates
Todoist tasks, organized into sections named after each course - skipping
duplicates."""

import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

from .canvas_client import CanvasClient
from .config import COMPLETED_LOOKBACK_DAYS, Settings
from .models import Assignment, TodoistTask
from .state import load_state, prune_canvas_tasks, save_state
from .sync_logic import (
    assign_priority,
    filter_assignments,
    format_due_date,
    format_due_datetime,
    todoist_due_matches,
)
from .todoist_client import TodoistClient

log = logging.getLogger(__name__)

PRIORITY_LABELS = {4: "p1", 3: "p2", 2: "p3", 1: "p4"}


@dataclass
class VanishedClassification:
    """How to treat tasks that were active last run but missing from Todoist now."""

    completed: list[str]
    removed: list[str]
    pending: set[str]


def classify_vanished_tasks(prev_active, existing_tasks, completed_names):
    """Classify vanished tracked tasks using the completed-tasks API when available.

    completed_names: set of task content from the completed API, or None if the fetch failed.
    """
    vanished = prev_active - set(existing_tasks)
    if not vanished:
        return VanishedClassification([], [], set())

    if not existing_tasks:
        if prev_active:
            log.info("  No Todoist tasks returned - skipping completion detection this run.")
        return VanishedClassification([], [], vanished)

    if completed_names is None:
        log.warning(
            "  Completed-tasks fetch failed - suppressing creation for vanished tasks this run."
        )
        return VanishedClassification([], [], vanished)

    completed = []
    removed = []
    for name in sorted(vanished):
        if name in completed_names:
            completed.append(name)
        else:
            removed.append(name)
    return VanishedClassification(completed, removed, set())


def reopen_tasks_in_todoist(existing_tasks, completed_tasks, removed_tasks):
    """Drop skip state when a task reappears in Todoist's active list."""
    for name in list(completed_tasks):
        if name in existing_tasks:
            del completed_tasks[name]
            log.info(f"  Reopened (uncompleted in Todoist): {name}")
    for name in list(removed_tasks):
        if name in existing_tasks:
            del removed_tasks[name]
            log.info(f"  Reopened (recreated in Todoist): {name}")


def should_skip_completed(assignment: Assignment, completed_tasks, canvas_due_changed, now):
    """True if the task was completed in Todoist and should be left alone.

    If the Canvas deadline moved into the future, the task is treated as
    reopened: it is removed from completed_tasks and syncing proceeds.
    """
    if completed_tasks is None or assignment.task_name not in completed_tasks:
        return False
    if canvas_due_changed and assignment.due_at > now:
        del completed_tasks[assignment.task_name]
        log.info(f"    Reopened (deadline changed): {assignment.name}")
        return False
    log.info(f"    Skipped (completed in Todoist): {assignment.name}")
    return True


def should_skip_removed(assignment: Assignment, removed_tasks, canvas_due_changed, now):
    """True if the user removed the task in Todoist and it should not be recreated."""
    if removed_tasks is None or assignment.task_name not in removed_tasks:
        return False
    if canvas_due_changed and assignment.due_at is not None and assignment.due_at > now:
        del removed_tasks[assignment.task_name]
        log.info(f"    Reopened (deadline changed): {assignment.name}")
        return False
    log.info(f"    Skipped (removed in Todoist): {assignment.name}")
    return True


def build_create_payload(assignment: Assignment, due_datetime, priority, project_id, section_id):
    payload = {
        "content": assignment.task_name,
        "due_datetime": due_datetime,
        "priority": priority,
    }
    if assignment.url:
        payload["description"] = assignment.url
    if project_id:
        payload["project_id"] = project_id
    if section_id:
        payload["section_id"] = section_id
    return payload


def sync_assignment(
    assignment: Assignment,
    todoist: TodoistClient,
    settings: Settings,
    project_id,
    section_id,
    existing_tasks,
    completed_tasks=None,
    removed_tasks=None,
    suppress_create_names=None,
    prev_canvas_due=None,
):
    """Create, update, or skip the Todoist task for one assignment.

    Returns "created", "updated", or "skipped". Mutates existing_tasks (and
    completed_tasks / removed_tasks on reopen) to reflect what was done.
    """
    task_name = assignment.task_name
    due_date = format_due_date(assignment.due_at, settings.timezone)
    due_datetime = format_due_datetime(assignment.due_at)
    current_canvas_due = assignment.due_at.isoformat()
    priority = assign_priority(assignment.due_at)
    p_label = PRIORITY_LABELS[priority]
    canvas_due_changed = prev_canvas_due is not None and prev_canvas_due != current_canvas_due
    now = datetime.now(timezone.utc)

    if should_skip_completed(assignment, completed_tasks, canvas_due_changed, now):
        return "skipped"
    if should_skip_removed(assignment, removed_tasks, canvas_due_changed, now):
        return "skipped"

    existing = existing_tasks.get(task_name)
    if existing is not None:
        due_matches = todoist_due_matches(existing, due_date, due_datetime)
        if not canvas_due_changed and existing.priority == priority and due_matches:
            log.info(f"    Skipped (exists): {assignment.name}")
            return "skipped"

        update_payload = {}
        if existing.priority != priority:
            update_payload["priority"] = priority
        if canvas_due_changed or not due_matches:
            update_payload["due_datetime"] = due_datetime

        r = todoist.update_task(existing.id, update_payload)
        if r.status_code == 200:
            existing.priority = priority
            existing.due_date = due_date
            existing.due_datetime = due_datetime
            reason = "deadline changed" if canvas_due_changed else "updated"
            log.info(f"    Updated ({p_label}, {reason}): {assignment.name} - due {due_date}")
            return "updated"
        log.info(f"    Failed to update: {assignment.name} - {r.status_code}: {r.text[:150]}")
        return "skipped"

    if suppress_create_names and task_name in suppress_create_names:
        log.info(f"    Skipped (vanished, awaiting classification): {assignment.name}")
        return "skipped"

    payload = build_create_payload(assignment, due_datetime, priority, project_id, section_id)
    r = todoist.create_task(payload)
    if r.status_code == 200:
        existing_tasks[task_name] = TodoistTask(
            id=r.json().get("id"),
            priority=priority,
            due_date=due_date,
            due_datetime=due_datetime,
        )
        log.info(f"    Created ({p_label}): {assignment.name} - due {due_date}")
        return "created"
    log.info(f"    Failed: {assignment.name} - {r.status_code}: {r.text[:150]}")
    return "skipped"


def get_upcoming_assignments(canvas, course_id, course_name, now, cutoff, tracked_task_names):
    try:
        raw = canvas.get_assignments(course_id)
    except requests.RequestException:
        log.info(f"  Could not fetch assignments for {course_name}")
        return []
    return filter_assignments(raw, course_name, now, cutoff, tracked_task_names)


def get_or_create_section(todoist, name, project_id, section_cache):
    if name in section_cache:
        return section_cache[name]
    section_id = todoist.create_section(name, project_id)
    section_cache[name] = section_id
    log.info(f"  Created new section: {name}")
    return section_id


def main(settings: Settings | None = None):
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    settings = settings or Settings.from_env()
    canvas = CanvasClient(settings)
    todoist = TodoistClient(settings)

    log.info(f"\n{'=' * 55}")
    log.info(f"  Canvas to Todoist Sync  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    log.info(f"  Window: next {settings.days_ahead} days")
    log.info(f"{'=' * 55}\n")

    project_id = todoist.get_project_id(settings.project_name)
    if not project_id:
        log.warning(f"  WARNING: Project '{settings.project_name}' not found - sections will be skipped.")

    section_cache = todoist.get_sections(project_id) if project_id else {}
    existing_tasks = todoist.get_tasks(project_id)

    state = load_state(settings.state_file)
    prev_active = set(state["active_tasks"])
    completed_tasks = state["completed_tasks"]
    removed_tasks = state["removed_tasks"]
    prev_canvas_tasks = state["canvas_tasks"]

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=settings.days_ahead)

    reopen_tasks_in_todoist(existing_tasks, completed_tasks, removed_tasks)

    try:
        since = now - timedelta(days=COMPLETED_LOOKBACK_DAYS)
        completed_names = todoist.get_completed_tasks_by_completion_date(project_id, since, now)
    except requests.RequestException as exc:
        log.warning(f"  Could not fetch completed tasks from Todoist: {exc}")
        completed_names = None

    classification = classify_vanished_tasks(prev_active, existing_tasks, completed_names)
    if classification.completed:
        now_ts = now.isoformat()
        for name in classification.completed:
            completed_tasks[name] = now_ts
            removed_tasks.pop(name, None)
        log.info(
            f"  Detected {len(classification.completed)} newly completed task(s) — will skip recreating."
        )
        for name in classification.completed:
            log.info(f"    Completed: {name}")
        log.info("")
    if classification.removed:
        now_ts = now.isoformat()
        for name in classification.removed:
            removed_tasks[name] = now_ts
            completed_tasks.pop(name, None)
        log.info(
            f"  Detected {len(classification.removed)} removed task(s) in Todoist — will skip recreating."
        )
        for name in classification.removed:
            log.info(f"    Removed: {name}")
        log.info("")

    suppress_create_names = classification.pending

    tracked_task_names = set(prev_canvas_tasks.keys())

    total_created = 0
    total_updated = 0
    total_skipped = 0
    # Carry over previous tracking so assignments not returned this run
    # (dropped courses, pagination hiccups) keep their deadline history.
    canvas_tasks = prune_canvas_tasks(dict(prev_canvas_tasks), now=now)

    for course in canvas.get_active_courses():
        course_name = course["name"]
        log.info(f"Course: {course_name}")
        assignments = get_upcoming_assignments(
            canvas, course["id"], course_name, now, cutoff, tracked_task_names
        )

        if not assignments:
            log.info("   No upcoming assignments.\n")
            continue

        section_id = None
        if project_id:
            section_id = get_or_create_section(todoist, course_name, project_id, section_cache)

        for a in assignments:
            if a.due_at is None:
                # Due date removed in Canvas: keep tracking, leave the
                # Todoist task untouched.
                canvas_tasks[a.task_name] = None
                log.info(f"    Skipped (due date removed in Canvas): {a.name}")
                total_skipped += 1
                continue
            canvas_tasks[a.task_name] = a.due_at.isoformat()
            result = sync_assignment(
                a,
                todoist,
                settings,
                project_id,
                section_id,
                existing_tasks,
                completed_tasks,
                removed_tasks,
                suppress_create_names,
                prev_canvas_due=prev_canvas_tasks.get(a.task_name),
            )
            if result == "created":
                total_created += 1
            elif result == "updated":
                total_updated += 1
            else:
                total_skipped += 1
        log.info("")

    # Only Canvas-synced tasks that currently exist in Todoist count as active.
    active_canvas = {name for name in canvas_tasks if name in existing_tasks}
    save_state(settings.state_file, active_canvas, completed_tasks, canvas_tasks, removed_tasks)

    log.info(f"{'-' * 55}")
    log.info(f"  Done: {total_created} created, {total_updated} updated, {total_skipped} skipped")
    log.info(f"{'-' * 55}\n")
