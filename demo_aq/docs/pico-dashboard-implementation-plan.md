# Pico Dashboard Styling Implementation Plan

> **For agentic workers:** Execute inline in the current workspace; do not
> create a worktree or subagents.

**Goal:** Apply Pico CDN styling and minimal inline dashboard refinements while
removing metadata from the table.

**Architecture:** The single dashboard template loads Pico classless CSS from
its CDN, then applies a small inline override block. Existing SSE and row-upsert
logic remain unchanged except for removing the metadata cell.

**Tech Stack:** Pico CSS CDN, inline CSS, inline browser JavaScript.

## Global Constraints

- No local static assets or new dependencies.
- Keep inline JavaScript and SSE behavior unchanged.
- Display columns are ID, state, message, queued, finished, and timeout.
- Automated demo tests remain intentionally skipped; verify template rendering
  and Django configuration directly.

---

### Task 1: Style and simplify the dashboard template

**Files:**
- Modify: `demo_aq/dashboard/templates/dashboard/index.html`

**Consumes:** Existing SSE entry keys `id`, `state`, `message`, `queued_at`,
`finished_at`, and `timeout_seconds`.

**Produces:** A Pico-styled responsive table without a metadata column.

- [x] **Step 1: Load Pico and add local layout rules**

  Add the Pico CDN stylesheet in `<head>`. Add inline CSS for a constrained
  `main` width, title/action alignment, horizontal table scrolling, status
  badges, monospace clipped IDs, and existing non-wrapping timestamp cells.

- [x] **Step 2: Remove metadata from markup and JavaScript**

  Delete the `Metadata` heading, create six cells per row, and remove
  `JSON.stringify(entry.metadata)` from `render()` values. Apply state-specific
  badge classes from `entry.state` and ID/timestamp classes to their cells.

- [x] **Step 3: Directly verify the rendered page**

  Run `python manage.py check`, render `/`, and inspect that the Pico CDN link,
  six headings, EventSource path, and no metadata text are present. Run
  `git diff --check`.
