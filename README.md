# canvas-todoist-sync

Pulls Canvas assignments due in the next 30 days and creates/updates Todoist
tasks, organized into sections named after each course. Deadline changes in
Canvas are synced to Todoist; tasks you complete in Todoist are not recreated.

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
python sync.py
# or
python -m canvas_todoist_sync
```

Or via the wrapper script (used by launchd/cron; logs to `sync_log.txt`):

```bash
./run_sync.sh
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Layout

- `canvas_todoist_sync/config.py` — `Settings` loaded from env, shared constants
- `canvas_todoist_sync/models.py` — `Assignment` / `TodoistTask` dataclasses
- `canvas_todoist_sync/sync_logic.py` — pure logic: filtering, dates, priorities
- `canvas_todoist_sync/state.py` — `sync_state.json` load/save/prune/migration
- `canvas_todoist_sync/canvas_client.py` — Canvas API (HTTP only)
- `canvas_todoist_sync/todoist_client.py` — Todoist API (HTTP + pagination)
- `canvas_todoist_sync/sync.py` — orchestration (`main`)
- `sync.py` — backward-compatible entry-point shim
