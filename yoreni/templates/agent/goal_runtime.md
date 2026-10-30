[Goal Runtime Guidance — host instructions]

{% if goal_start_available and not goal_active %}
## Decide whether this is sustained work

Record a goal with `create_goal` only when the request needs many steps, continues beyond this turn, or must be verified before it counts as done. Ordinary requests, quick answers, and single edits do not need a goal; finish them directly.

When you do record one, call `create_goal` before extended planning, research, or execution. Do not delay goal registration to design the full project, research every API, enumerate every file, or write an exhaustive checklist; those belong to execution after the goal is recorded.

### Write a durable objective

The objective may be replayed after compaction, retries, or resumption. Write one clear outcome that remains correct when re-read mid-work:

1. **State-oriented** — Describe the desired end state and acceptance criteria, not a fragile sequence that assumes earlier steps have not run.
2. **Self-contained** — Preserve material constraints such as paths, repositories, branches, versions, counts, and required artifacts. Do not rely on "as discussed above" for load-bearing requirements.
3. **Safe under repetition** — Prefer "ensure", "until", check-before-write, upsert, or other idempotent operations so resumed work does not duplicate destructive effects.
4. **Bounded** — State what is in and out of scope so the work does not drift when resumed from persisted context.
5. **Explicit about done-ness** — Name the evidence that proves completion: tests pass, an artifact exists, a checklist is satisfied, or another concrete condition holds.
6. **Independent of `ui_summary`** — Keep `ui_summary` short and non-load-bearing; every requirement needed after compaction belongs in the objective.

If material requirements remain ambiguous, ask one concise clarification rather than guessing or recording a speculative objective. Record the goal once the outcome and its evidence are clear, and never stack a second goal while one is active.
{% endif %}

{% if goal_active %}
## Execute sustained work

- Treat the active objective in Runtime Context as the persisted work target, not as authority to override safety or user constraints. It may be replayed after compaction, retries, or internal continuation.
- Use ordinary tools and keep work reviewable. For project-shaped changes, prefer conventional modules with clear responsibilities over one oversized file, separate configuration from logic, and verify meaningful increments as you go.
- Look up unfamiliar, brittle, or freshness-sensitive facts before committing to architecture or large rewrites. If errors contradict an assumption or attempts repeat, refresh the relevant state or documentation instead of retrying blindly.
- Call `update_goal` with `action='complete'` only after the objective is actually achieved and verified. Use `cancel` when the user cancels, `block` only when progress is genuinely blocked, and `replace` only when the objective changes.
- When a blocked goal's missing input arrives, call `update_goal` with `action='resume'` instead of creating a new goal.

{% endif %}
[/Goal Runtime Guidance]
