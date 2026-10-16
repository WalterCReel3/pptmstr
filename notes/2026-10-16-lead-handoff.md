# Lead handoff, ui-rework, 2026-10-16

Written by the lead before context compaction, for whoever leads next, including the same
session after its context is summarised. The board and the planning records are the source of
truth; this note is the working state and the process lessons they do not hold.

## Where the records are

- `planning/2026-10-15-ui-rework.md`: the rework's scope and every operator decision in their
  words: round one, round two, the checkpoint, DETAIL in FOCUS, the margins, the fleet, the
  splash, the bookmarks idea.
- `planning/2026-10-15-ui-style-probe.md`: the style pick, per-theme values, tabs at 15 px, and
  the hint and well calls.
- `planning/2026-10-15-tool-block-variants.md`: the tool-block proofs and sign-off.
- `planning/2026-10-16-link-variants.md`, `planning/2026-10-16-fleet-empty-variants.md`
- `planning/2026-10-16-an-answer-wakes-its-asker.md`: the notification decision (option a).
- `notes/2026-10-15-the-cli-runs-git-in-its-cwd.md`: the CLI git and settings exposure, every
  probe result, and the operator's choice to change nothing about settings loading.
- `notes/2026-10-16-pane-margins-and-launcher-placement.md`

## State at the time of writing (HEAD 4438b46 plus uncommitted work in the tree)

- The last full `make check` was at checkpoint 4d94a32: exit 0, 12507 passed, 8 skipped,
  24 xfailed.
- **Uncommitted in the tree,** completed and not yet committed: baseline-sites, theme-fix-2,
  diff-parity. They share files with running tasks (launcher.py, transcript_pane.py), so commit
  them only after gate-2, as named files. Never `git add -A`: the worktree root holds empty
  sandbox placeholder files (.bashrc, .mcp.json, .claude/settings.json, …) that must not be
  committed.
- **Running when this was written:** a builder on launcher-place (lead approved its widgets.py
  begin_dialog_body edit); a builder on turn-boundary (spec v3, porting the draft at
  /tmp/claude-1000/turnb/draft), which then moves on to pane-margins.
- **Queued, in the order the board forces:** pane-margins → scrollbar-gutter → link-control;
  detail-header → history-to-digest → wake-asker → detail-actions; fleet-dock → fleet-empty →
  splash-off; ini-isolation; rv-tool-block-fix; then gate-2 (its spec lists every task it
  waits on).
- **Withdrawn, and inert:** delete-rich-pane (completed as withdrawn), font-raster-probe,
  empty-bookmarks (pending; nothing depends on it).
- **With the operator:** command cmd-20261016T123852Z-1061a16567c2, the protected-path probe
  (scripts/verify_gate_hook_write.py). When it has run, read it with read_commands and fill the
  "not yet run" table in the CLI note.

## Process lessons from this session (the operator corrected the lead on each)

1. **Check `read_decisions` and `read_inbox` at the start of every turn.** Nothing notifies the
   lead of an answer (wake-asker will change that). The lead repeatedly missed answered
   decisions and left builders waiting 25-75 minutes on concerns.
2. **Answer a builder's "may I edit X" within minutes.** Every unanswered one ended in a fallback
   or an incident.
3. **Never cite a lead default back to the operator as their requirement.** The reserved
   scrollbar strip (U4) was the lead's default, accepted only "for now", and the lead later
   quoted it as "the default you accepted". The operator: "Could be LLM output taken as gospel
   again."
4. **A catch with a lasting cost is its own explicit yes/no before anything is boarded.**
   `rich_pane` was boarded for deletion on a "yes" to a recommendation whose catch was one line
   under "what it buys"; the operator reversed it. Also: state the catch correctly. The
   setting_sources decision wrongly said CLAUDE.md was unaffected.
5. **"Looks like the current state" may still be a request.** U7 (the fleet dock) was read as no
   change, and it was an open request.
6. **The operator: "You shouldn't be waiting on _anything_."** Where the operator has not picked,
   build the stated default, record it as the lead's, and keep going.
7. **Measure before fixing,** with a positive control for every "no" (the trust probe's e0 arm
   is the model). Several reviewer blockers were refuted by a run (tab hover) and several
   confirmed (locked worktree reuse, decoy filters).
8. **The session's tool policy refuses TaskStop, SendMessage and ListAgents.** A running agent
   can only be reached through a board concern addressed to its exact name (builder-N).
