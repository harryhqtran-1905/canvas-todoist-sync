"""Sync state persistence: JSON load/save, legacy migration, pruning."""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from .config import COMPLETED_EXPIRY_DAYS, STALE_TRACKING_DAYS
from .sync_logic import is_canvas_task_name

log = logging.getLogger(__name__)


def load_state(state_file):
    if os.path.exists(state_file):
        with open(state_file) as f:
            data = json.load(f)
            if isinstance(data.get("completed_tasks"), list):
                now_ts = datetime.now(timezone.utc).isoformat()
                data["completed_tasks"] = {name: now_ts for name in data["completed_tasks"]}
            data.setdefault("canvas_tasks", {})
            # Migration: older versions tracked every Todoist task (including
            # personal ones). Keep only Canvas-synced names.
            canvas_names = set(data["canvas_tasks"])
            data["active_tasks"] = [
                n for n in data.get("active_tasks", [])
                if n in canvas_names or is_canvas_task_name(n)
            ]
            data["completed_tasks"] = {
                n: ts for n, ts in data["completed_tasks"].items()
                if n in canvas_names or is_canvas_task_name(n)
            }
            data.setdefault("removed_tasks", {})
            data["removed_tasks"] = {
                n: ts for n, ts in data["removed_tasks"].items()
                if n in canvas_names or is_canvas_task_name(n)
            }
            return data
    return {"active_tasks": [], "completed_tasks": {}, "removed_tasks": {}, "canvas_tasks": {}}


def prune_canvas_tasks(canvas_tasks, now=None):
    """Drop tracked tasks whose due date is long past (dropped courses, etc.)."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=STALE_TRACKING_DAYS)
    kept = {}
    for name, due in canvas_tasks.items():
        if due is not None and datetime.fromisoformat(due) < cutoff:
            continue
        kept[name] = due
    removed = len(canvas_tasks) - len(kept)
    if removed:
        log.info(f"  Pruned {removed} stale tracked Canvas task(s) from state.")
    return kept


def save_state(state_file, active_tasks, completed_tasks, canvas_tasks, removed_tasks=None):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=COMPLETED_EXPIRY_DAYS)).isoformat()
    pruned = {name: ts for name, ts in completed_tasks.items() if ts >= cutoff}
    removed = len(completed_tasks) - len(pruned)
    if removed:
        log.info(f"  Pruned {removed} expired completed task(s) from state.")
    removed_tasks = removed_tasks or {}
    pruned_removed = {name: ts for name, ts in removed_tasks.items() if ts >= cutoff}
    removed_count = len(removed_tasks) - len(pruned_removed)
    if removed_count:
        log.info(f"  Pruned {removed_count} expired removed task(s) from state.")
    with open(state_file, "w") as f:
        json.dump(
            {
                "active_tasks": list(active_tasks),
                "completed_tasks": pruned,
                "removed_tasks": pruned_removed,
                "canvas_tasks": canvas_tasks,
            },
            f,
            indent=2,
        )
