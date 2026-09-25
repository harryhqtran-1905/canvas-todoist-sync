# canvas-todoist-sync

Pulls Canvas assignments due in the next 30 days and creates Todoist tasks,
organized into sections named after each course.

- Each assignment is linked to its Todoist task by ID, so renames don't break tracking.
- **Todoist decides what's done.** A task you complete or delete in Todoist
  is never recreated or reopened. Uncheck it in Todoist to bring it back,
  and it picks up the current Canvas due date.
- When Canvas changes a deadline, the open task is updated in place. Due dates
  you set yourself are left alone unless Canvas changes the deadline again.
- Priority rises as the deadline approaches. If you change it yourself, the sync
  will only ever raise it.
- If Todoist can't be reached, the run stops without changing anything and
  the next run, 30 minutes later, tries again. If a single Canvas course fails
  to load, that course is skipped for the run and the others still sync.

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
CANVAS_API_TOKEN=...
CANVAS_BASE_URL=https://your-school.instructure.com
TODOIST_API_TOKEN=...
TODOIST_PROJECT_NAME=School   # optional, defaults to "School"
TIMEZONE=America/Los_Angeles  # optional, defaults to America/Los_Angeles
```

## Run

```bash
python -m canvas_todoist_sync
```

Preview what a run would do without touching Todoist or the state file:

```bash
python -m canvas_todoist_sync --dry-run
```

## Deploy (systemd, every 30 minutes)

The sync is meant to run on one always-on host; it is the only writer of
Todoist and of `sync_state.json`. Units are in `deploy/` and assume the repo
lives at `/opt/canvas-todoist-sync` with a virtualenv in `.venv`.

```bash
sudo cp deploy/canvas-todoist-sync.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now canvas-todoist-sync.timer
systemctl list-timers canvas-todoist-sync.timer   # next run
journalctl -u canvas-todoist-sync.service -n 50   # last output
```

The timer starts one run every 30 minutes and never starts a second run while
one is still going. Each request has a 30-second timeout and is retried 4 times.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Layout

- `canvas_todoist_sync/config.py` — `Settings` loaded from env, shared constants
- `canvas_todoist_sync/models.py` — `Assignment`, `TodoistTask`, `Link` dataclasses
- `canvas_todoist_sync/http_session.py` — shared session with request timeout and retry/backoff
- `canvas_todoist_sync/canvas_client.py` — Canvas API (HTTP only)
- `canvas_todoist_sync/todoist_client.py` — Todoist API (HTTP + pagination, task status by ID)
- `canvas_todoist_sync/store.py` — `sync_state.json` v2: links by Canvas assignment, atomic save, v1 upgrade
- `canvas_todoist_sync/plan.py` — pure decisions: what to create/update/leave alone
- `canvas_todoist_sync/apply.py` — executes the plan against Todoist
- `canvas_todoist_sync/sync_logic.py` — pure date and priority helpers
- `canvas_todoist_sync/sync.py` — orchestration: fetch → plan → apply → save (`main`, `--dry-run`)
- `sync.py` — backward-compatible entry-point shim
- `deploy/` — systemd service and timer (one run every 30 minutes)
