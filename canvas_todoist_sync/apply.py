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
