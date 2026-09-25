# ID-based sync follow-ups

Do not implement this file in the same pass as the ID-based sync. It records review bugs and architecture deepenings for the next change. Live account steps from the original plan (LaunchAgent retirement, `sync_state` backup, real `--dry-run`, `./run_sync.sh`, systemd enable) were not run.

Behavior spec at the top of `docs/superpowers/plans/2026-09-25-id-based-sync.md` wins where a later task contradicts it.

## Bugs

### Critical — First link overwrites a manual due date and can lower a manual priority

`plan.py` `_link_existing` stores `canvas_due=None` and `synced_priority` equal to the task’s current priority. The update path treats `None` as “Canvas changed,” writes the Canvas due, and skips the “replaced your due date” note because `synced_due` was just copied from the task. The same copy makes the current priority look sync-owned, so the deadline formula may lower it.

Why: the behavior spec says a manual due stays when Canvas is unchanged, and a manual priority is only raised. This path is the v1 upgrade and a lost state file, so the first real run will do it. Task 7’s comment asks for the `None` re-check; that comment loses to the spec. Tests only use matching dues, so they stay green.

### Important — Stale links are pruned before this run’s Canvas due is known

`run` calls `prune_links` on the stored `canvas_due`, then fetches assignments. A completed link older than 60 days is dropped. If Canvas then shows that assignment inside the 30-day window, there is no link and no name match, so a new task is created.

Why: that is the original recreate bug on a narrower path. It also fights “a completed or deleted task stays that way when the Canvas due date changes.” A course whose assignment list fails is pruned anyway, so links the spec says must stay can disappear before the skip.

### Important — A missing School project syncs the whole account

`get_project_id` returning `None` logs a warning and continues. `get_active_tasks(None)` omits `project_id`. Creates then have no project, and a name match in another project becomes a permanent id link.

Why: “open but outside the project, leave it alone” cannot be enforced if the project was never found. With id links this is worse than the old name-based warning.

### Important — v1 state that the old bug already emptied cannot protect finished work

`_from_v1` only carries names still in `active_tasks`, `completed_tasks`, and `removed_tasks`. A state file saved as `active_tasks: []` after the project emptied has no names. Those finished assignments are created again.

Why: the upgrade cannot see Todoist history that the previous sync already forgot. The first dry run on the real account has to be checked for `would create` lines before any write. That dry run was not done here.

### Important — Tests do not cover the three cases above

No test shows a failed course leaving links unchanged, a rescheduled 60-day-old completed link staying completed, or a first link leaving a different Todoist due and a higher manual priority alone.

Why: `test_run` and `test_plan` pass while those spec rows fail.

### Minor — A failed Todoist write still exits 0

`apply_plan` counts the failure and `main` returns success. systemd marks the oneshot successful. The `Done: … failed` line is the only signal.

Why: a tick that created nothing and failed every write looks healthy in the journal.

### Minor — Duplicate open task names collapse to one

`unlinked_by_name` is keyed by content, so the last duplicate wins. Leftover copies from the old recreate bug stay in Todoist and are not linked.

Why: the first run will not clean them up, and only one id is stored.

### Minor — `save_state` does not fsync before replace

A power loss can replace a good state file with an empty one. The next run then fails `json.loads` and stays down until the file is restored.

Why: the replace is atomic for the directory entry and not durable for the bytes.

### Minor — A create response without an id skips the save

`r.json()["id"]` raising `KeyError` is outside `requests.RequestException`, so `run` never reaches `save_state` even if earlier creates in the same plan succeeded.

Why: the next run can link by name, and a second task with the same name can attach to the wrong id.

### Minor — README states only half of the priority rule

The README says priority rises, and that a manual change is only raised. Sync-owned priority also falls when a deadline moves out. The code and the spec do that.

Why: the doc will surprise someone who lowered a deadline and expected the priority to stay.

## Architecture

### Strong — First link is its own decision inside the planner

Files: `plan.py`, `store.py`.

Why: null `canvas_due` is doing the work of a missing concept. Deepen `build_plan` so adopting an existing task stores the due already on the task, pushes a due only after a later Canvas change, and leaves priority unset until the sync has written one. Keep v1 `canvas_tasks` across load so the upgrade can tell “Canvas moved it” from “you moved it.” Tests stay on `build_plan`.

### Strong — Prune and abort live inside `run`

Files: `sync.py`, `store.py`.

Why: `run` is a shallow sequence. The stale-link bug and the missing-project bug are the order of calls, not the helpers. Deepen `run` so a failed course is skipped with its links untouched, prune uses the due date observed this run, and a missing project aborts before any write. Tests stay on `run`.

### Worth exploring — One Todoist seam, two adapters

Files: `todoist_client.py`, `fakes.py`, `sync.py`.

Why: the HTTP adapter and `FakeTodoist` are already two adapters, which justifies the seam. A missing project should fail at that seam in both, instead of `None` leaking into `run` as “read every open task.”

## Contradictions recorded while implementing

- Task 7’s `canvas_due=None` re-check contradicts the spec rows for manual due dates and manual priority. The committed code follows Task 7. The critical bug above is that choice.
- Task 10 prunes before fetch. That contradicts “a failed course’s links stay” and “a completed task stays completed when the due date changes.” The committed code follows Task 10.
- Expected pytest totals in the plan drifted by one after the model tests (49 passed where the plan said 48, then 55 and 62). Behavior was unchanged.
- Task 8 said the seven new tests fail. Two already passed because they assert no update, which the unfinished planner already produced.
