# Code Review: canvas-todoist-sync

Status update (2026-07-06): items #1, #2, #3, #5, #6, #7, #10, #11, #12, #13, #14 are fixed and moved to the Solved section below. #4 and #9 are partially solved (notes inline). #8 remains open.

## Refactor Review (2026-07-06)

The monolithic `sync.py` (~493 lines) was refactored into the `canvas_todoist_sync/` package (`config`, `models`, `sync_logic`, `state`, `canvas_client`, `todoist_client`, `sync`), with a thin root `sync.py` shim for backward compatibility. Diffed function-by-function against the original; no logic regressions found. Tests grew 23 → 31 (`detect_newly_completed`, `should_skip_completed` now covered). Findings from reviewing the refactor:

### R1. Sync output moved from stdout to stderr — FIXING

The refactor replaced `print()` with `logging`. `logging.basicConfig` defaults to a stderr stream handler, so all sync output (not just errors) now goes to stderr. `run_sync.sh` merges streams (`>> sync_log.txt 2>&1`) so it is unaffected, but a launchd plist with separate `StandardOutPath`/`StandardErrorPath` (implied by `launchd_stderr.log` in `.gitignore`) would misroute normal output into the stderr log. Fix: configure the logging handler to use stdout.

### R2. `logging.basicConfig` on the root logger inside `main()` — OPEN

Configuring the root logger is a global side effect. Harmless for the cron script, but if `main()` is imported and called elsewhere it takes over that program's logging (and `basicConfig` is a no-op when handlers already exist, which could suppress the sync's own output). Prefer a package-level logger or moving config to the entry points.

### R3. Fetch failures logged at INFO level — OPEN

`get_upcoming_assignments` swallows `requests.RequestException` and logs at INFO. With real log levels now available, a swallowed API failure should be `log.warning`. (Pre-existing behavior — was a `print` — but the refactor was the moment to raise it.)

### R4. Remaining test gaps — OPEN

Now-testable but still uncovered: `sync_assignment`'s create/update/skip matrix (stub `TodoistClient`), `TodoistClient.get_tasks` due-parsing fallback (datetime in `due.date` only), and `load_state`'s legacy list→dict `completed_tasks` migration.

### R5. Nits — OPEN

- `should_skip_completed` reads as a pure predicate but mutates `completed_tasks` on the reopen path (behavior preserved; name hides the side effect).
- Mixed typing styles (`Optional[...]` in `models.py` vs `X | None` in `sync.py`; `filter_assignments` annotated as bare `-> list`).

---

## Open

### 4. Completed-task guard blocks deadline updates after professor changes due date — PARTIALLY SOLVED

If a Canvas task was marked completed (correctly or via false positive in #3), `create_todoist_task` skips it entirely — including deadline updates. A reopened or extended assignment in Canvas will not sync until the 105-day expiry window passes.

> Status: partially solved. When the Canvas due date changes to a future date, the task is now removed from `completed_tasks` and recreated ("Reopened (deadline changed)" path in `create_todoist_task`). However, a false-positive completed state still blocks recreation when the due date has NOT changed.

---

### 8. Completion detection has false-positive paths — OPEN

```298:304:sync.py
    newly_gone = prev_active - set(existing_tasks.keys())
    ...
        if due_str and datetime.fromisoformat(due_str) < now:
            continue  # due date passed — expired, not completed
        newly_completed.append(t)
```

A Canvas task is treated as completed when it disappears from the active task list. That also happens if:

- The task was moved to another project
- The task was renamed
- Pagination/API transient failure omitted it from `get_existing_todoist_tasks`

Once marked completed, it will not be recreated or updated (#4).

> Status: open. Mitigations added: tracking is now restricted to Canvas-synced tasks in the sync project, and completion detection is skipped when the Todoist fetch returns zero tasks. But completion is still inferred from a name diff, so moving a task out of the project or renaming it is still treated as completion. Adversarial review recommendation: track Todoist task IDs and confirm actual completion/deletion via the Todoist API before adding to `completed_tasks`.

---

### 9. Assignments with removed due dates are not handled — PARTIALLY SOLVED

```93:95:sync.py
        due_str = a.get("due_at")
        if not due_str:
            continue
```

If a professor removes a due date, the assignment is ignored. The Todoist task keeps the old deadline, and tracking is dropped from `canvas_tasks` (#1). There is no cleanup or "remove due date" path.

> Status: partially solved. Tracked assignments with a removed due date are now kept in `canvas_tasks` with a `null` value, so tracking is no longer dropped and later deadline changes are detected. However, the existing Todoist task keeps its stale deadline — there is still no "clear due date in Todoist" path.

---

## Solved

### 1. `canvas_tasks` state is wiped for untracked / unprocessed assignments — SOLVED

```364:364:sync.py
    save_state(set(existing_tasks.keys()), completed_tasks, canvas_tasks)
```

`canvas_tasks` is rebuilt only from assignments returned this run. Anything not fetched is dropped from state:

- Dropped or inactive courses
- Assignments whose due date was removed in Canvas
- Assignments missed due to Canvas pagination (see #2)

After that, those tasks are no longer in `tracked_task_names`, so deadline changes in Canvas will not be detected or synced.

> Fixed: `canvas_tasks` is now seeded from the previous run's state and only entries seen this run are overwritten. `prune_canvas_tasks` drops entries more than 60 days past due so dropped courses don't accumulate forever.

---

### 2. Canvas assignment pagination is missing — SOLVED

```82:86:sync.py
    r = requests.get(
        f"{CANVAS_URL}/api/v1/courses/{course_id}/assignments",
        headers=CANVAS_HEADERS,
        params={"per_page": 50, "order_by": "due_at"}
    )
```

Only the first page (50 assignments) is read. Courses with more assignments will silently miss tasks. Same issue exists for courses (`per_page: 50` in `get_active_courses`).

> Fixed: `canvas_get_all` follows the `Link: rel="next"` header with `per_page: 100`, used by both `get_active_courses` and `get_upcoming_assignments`.

---

### 3. Non-Canvas Todoist tasks pollute completion tracking — SOLVED

```364:364:sync.py
    save_state(set(existing_tasks.keys()), completed_tasks, canvas_tasks)
```

`active_tasks` stores **every** Todoist task name, not just Canvas-synced ones (e.g. `"Laundry"`, `"LeetCode"` in your state file). When any of those is deleted or completed, it is added to `completed_tasks` and will be skipped if recreated for up to 105 days:

```228:230:sync.py
    if completed_tasks is not None and task_name in completed_tasks:
        print(f"    Skipped (completed in Todoist): {assignment['name']}")
        return "skipped"
```

This can block legitimate re-creation of personal tasks and creates false coupling between Canvas sync logic and unrelated Todoist usage.

> Fixed: `active_tasks` now only contains Canvas-synced task names that exist in Todoist. `load_state` migrates old state by filtering out names that are neither in `canvas_tasks` nor match the `[Course] Name` pattern.

---

### 5. Fragile `due_datetime` comparison may cause missed or repeated updates — SOLVED

```213:216:sync.py
def todoist_due_matches(existing, due_date, due_datetime):
    if existing.get("due_datetime"):
        return existing["due_datetime"] == due_datetime
    return existing.get("due_date") == due_date
```

Todoist may return datetimes as `2026-07-10T06:59:59`, `2026-07-10T06:59:59Z`, or `2026-07-10T06:59:59+00:00`, while the code always writes `...Z`. String equality can fail on equivalent timestamps, causing unnecessary API updates every run, or in edge cases inconsistent skip/update behavior.

Also, when Todoist stores only `due.date` as a datetime string (no `due.datetime`), the code falls back to date-only comparison and can miss time-only deadline changes on the same calendar day.

> Fixed: `parse_due_datetime` normalizes `Z`/`+00:00`/naive variants into aware UTC datetimes for comparison, and a datetime stored under `due.date` is now treated as a datetime instead of falling back to date-only.

---

### 6. No validation of required environment variables at startup — SOLVED

```18:20:sync.py
CANVAS_TOKEN  = os.getenv("CANVAS_API_TOKEN")
CANVAS_URL    = os.getenv("CANVAS_BASE_URL")
TODOIST_TOKEN = os.getenv("TODOIST_API_TOKEN")
```

If any are missing, the script proceeds and sends `Authorization: Bearer None`, producing opaque API failures instead of a clear early exit.

> Fixed: missing `CANVAS_API_TOKEN`, `CANVAS_BASE_URL`, or `TODOIST_API_TOKEN` now aborts at startup with a message listing the missing variables.

---

### 7. Global Todoist task fetch can cause name collisions — SOLVED

```110:132:sync.py
def get_existing_todoist_tasks():
    ...
        for t in data.get("results", []):
            ...
            tasks[t["content"]] = {
```

All active Todoist tasks are loaded globally. Duplicate task names across projects overwrite each other (last wins). Updates may target the wrong task, or a Canvas task may match a personal task with the same name.

> Fixed: `get_existing_todoist_tasks(project_id)` scopes the fetch to the sync project when it exists, so tasks in other projects can no longer collide.

---

### 10. `run_sync.sh` is machine-specific and brittle — SOLVED

```1:5:run_sync.sh
#!/bin/zsh
source /opt/homebrew/Caskroom/miniconda/base/etc/profile.d/conda.sh
conda activate dsci
cd ~/projects/canvas-todoist-sync
python sync.py >> sync_log.txt 2>&1
```

Hardcoded Homebrew Miniconda path and home directory. Will break on another machine or if conda is relocated. No `set -euo pipefail`; sync failures are only visible in the log.

> Fixed: `set -euo pipefail`, project directory derived from the script path, conda activation only if present and overridable via `CONDA_SH` / `CONDA_ENV`.

---

### 11. Assignment URL is fetched but never used — SOLVED

Canvas `html_url` is collected in `get_upcoming_assignments` but never added to the Todoist task description or comments. Minor missed UX value.

> Fixed: the Canvas URL is now set as the Todoist task `description` on create.

---

### 12. No `requirements.txt` or pinned dependencies — SOLVED

The project depends on `requests` and `python-dotenv` with no version pinning or documented install step. Reproducibility and deployment risk for a cron/launchd job.

> Fixed: added `requirements.txt` (pinned `requests`, `python-dotenv`), `requirements-dev.txt` (pinned `pytest`), and a `README.md` with setup/run instructions.

---

### 13. `launchd_stderr.log` not in `.gitignore` — SOLVED

If used by a launchd plist, it is a generated log file like `sync_log.txt` and should likely be ignored.

> Fixed: `launchd_stderr.log` and `launchd_*.log` added to `.gitignore`.

---

### 14. Newly created tasks store `id: None` in-memory — SOLVED

```354:356:sync.py
            if result == "created":
                total_created += 1
                existing_tasks[task_name] = {"id": None, **task_meta}
```

Harmless across runs (fresh fetch next time), but weak for same-run deduplication if duplicate assignment names appeared twice in one run.

> Fixed: the real task `id` from the Todoist create response is now stored in `existing_tasks`.

---

## Missing Tests — ADDRESSED

There are no tests. Highest-value coverage would be:

| Area | What to test |
|------|----------------|
| `normalize_todoist_due_date` | Date vs datetime string inputs |
| `todoist_due_matches` | Format variants (`Z`, `+00:00`, date-only fallback) |
| `get_upcoming_assignments` | In-window vs tracked-outside-window vs overdue tracked |
| Completion detection | Deleted task, expired due date, renamed task |
| `canvas_tasks` persistence | Tracked task not returned this run should not be dropped |
| `format_due_date` / `format_due_datetime` | Timezone boundary cases (UTC midnight → local date) |

> Addressed: `test_sync.py` (23 tests, passing) covers due-date normalization/parsing, `todoist_due_matches` format variants, timezone boundaries, assignment filtering (in-window, tracked-outside-window, removed due date), `prune_canvas_tasks`, and state migration. Completion-detection behavior (renamed/moved tasks) remains untested — see open item #8.

---

## Summary

The deadline-update work is directionally right (`prev_canvas_due`, tracked assignments, `due_datetime`), but reliability is undermined by **state loss in `canvas_tasks`**, **missing Canvas pagination**, and **completion tracking that mixes all Todoist tasks with Canvas ones**. Those three are the most important fixes before treating deadline sync as dependable.

> Update (2026-07-06): the three highest-priority issues above are fixed. Remaining open work per adversarial review: clear stale Todoist deadlines when Canvas removes a due date (#9), and make completion detection ID-based instead of name-diff-based (#8, which also closes the residual gap in #4).
