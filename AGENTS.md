# Agent Notes

If `.guide.yaml` exists, treat it as current local project state.

Read `.todo/context.json` at session start when present. It is a canonical
state-of-play handoff *for agents* — not a historical record, not a summary
for humans. It is JSON specifically so an agent can trim stale entries,
insert new ones, and add directives to its own section without reformatting
prose. Keep `last_updated`, `openspec_issue`, and `linear_issue_id` as its
first entries; use `null` when a reference does not apply. Retain only
current scope, decisions, constraints, dependencies, blockers, and the
limited recent history needed to resume work — remove stale detail when
adding new entries rather than appending indefinitely. Update it at the
start and end of any non-trivial task: a new change proposed, an
implementation landed, a decision that changed scope or approach, a Guide
phase change. Do not log routine verification output ("tests passed",
"lint clean") — record only what a future agent needs to resume work
without re-deriving it.
