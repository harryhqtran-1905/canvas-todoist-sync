"""Orchestration: fetch everything, plan, apply, save.

A Todoist failure or a failure loading the course list aborts the run before
anything is written, so a flaky network can never turn into duplicate or
resurrected tasks. A single course whose assignments fail to load is skipped
for this run and picked up again on the next 30-minute tick.
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
            log.warning(
                f"  Could not fetch assignments for {course['name']}"
                f" (left as is, retried next run): {exc}"
            )
            continue
        assignments.extend(
            select_assignments(raw, course["id"], course["name"], now, cutoff, linked_keys)
        )
    return assignments


def run(settings: Settings, canvas, todoist, dry_run=False, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = now + timedelta(days=settings.days_ahead)

    # 1. Fetch. Any exception here (Todoist, or the Canvas course list) aborts
    #    the run before anything is written. A single failing course is skipped
    #    inside fetch_assignments instead.
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
        log.error(f"  Aborted before making changes (next 30-minute run retries): {exc}")
        sys.exit(1)
