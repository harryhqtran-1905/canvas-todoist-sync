"""Sync state v2: one Link per Canvas assignment, saved atomically.

v1 files (keyed by task name) are converted on load: every name the old
sync knew about becomes a legacy entry, so the first v2 run neither
recreates finished work nor duplicates open tasks.
"""

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .config import LEGACY_STATE_DAYS, STALE_TRACKING_DAYS
from .models import COMPLETED, DELETED, Link

log = logging.getLogger(__name__)

STATE_VERSION = 2


@dataclass
class State:
    links: dict[str, Link] = field(default_factory=dict)
    # Task names from a v1 state file -> COMPLETED | DELETED. Consumed as
    # assignments get linked; dropped entirely once legacy_until passes.
    legacy: dict[str, str] = field(default_factory=dict)
    legacy_until: Optional[str] = None


def load_state(path, now=None) -> State:
    path = Path(path)
    if not path.exists():
        return State()
    data = json.loads(path.read_text())
    if data.get("version") != STATE_VERSION:
        return _from_v1(data, now or datetime.now(timezone.utc))
    return State(
        links={key: Link(**fields) for key, fields in data.get("links", {}).items()},
        legacy=data.get("legacy", {}),
        legacy_until=data.get("legacy_until"),
    )


def _from_v1(data, now) -> State:
    legacy = {}
    # A v1 "active" task that is still open in Todoist gets linked by name
    # before legacy is consulted; if it is gone, it was completed or deleted.
    for name in data.get("active_tasks", []):
        legacy[name] = COMPLETED
    for name in data.get("completed_tasks", {}):
        legacy[name] = COMPLETED
    for name in data.get("removed_tasks", {}):
        legacy[name] = DELETED
    log.info(f"  Upgraded sync state to v{STATE_VERSION} ({len(legacy)} task name(s) carried over).")
    until = (now + timedelta(days=LEGACY_STATE_DAYS)).isoformat()
    return State(legacy=legacy, legacy_until=until)


def prune_links(links, now) -> dict[str, Link]:
    """Drop links whose Canvas due date is long past (dropped courses, etc.)."""
    cutoff = now - timedelta(days=STALE_TRACKING_DAYS)
    kept = {
        key: link
        for key, link in links.items()
        if link.canvas_due is None or datetime.fromisoformat(link.canvas_due) >= cutoff
    }
    if len(kept) < len(links):
        log.info(f"  Pruned {len(links) - len(kept)} stale link(s) from state.")
    return kept


def save_state(path, state: State, now=None) -> None:
    """Write the state atomically: a crash mid-write leaves the old file intact."""
    now = now or datetime.now(timezone.utc)
    legacy, legacy_until = state.legacy, state.legacy_until
    if legacy_until and datetime.fromisoformat(legacy_until) <= now:
        legacy, legacy_until = {}, None
    payload = {
        "version": STATE_VERSION,
        "links": {key: asdict(link) for key, link in sorted(state.links.items())},
        "legacy": legacy,
        "legacy_until": legacy_until,
    }
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
