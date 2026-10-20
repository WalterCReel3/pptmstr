# Lead handoff, feat/inter-team-linking, 2026-10-19

Written by the lead before context compaction, for whoever leads next, including this session
after a resume. The board and the planning records are the source of truth. This note holds the
working state and the process lessons they don't.

**Resume this session to keep the board.** It is saved under this session's id. A new session
starts with an empty board and can't read this one.

## Where the records are

- `planning/2026-10-17-two-leads-in-one-window-share-a-channel.md`: Decided 1–13, each with the
  operator's words, its reasoning and later revisions. Settled questions are not to be
  re-argued.
- `planning/2026-10-18-building-the-inter-lead-channel.md`: rounds, status, "Decided during
  round two", and the carry lists **Into round three** and **Into round four**. The next round's
  scope comes from these.
- Research behind the rulings: `planning/2026-10-17-channel-topology/`,
  `planning/2026-10-17-sqlite-inspection/` and `planning/2026-10-18-peer-message-shape/`.
- `planning/2026-10-16-the-board-needs-graph-queries.md`: main's version, merged. Dry-run,
  `why_blocked` and `ready` are built. Lock-vs-edge, role-aware claims and touches enforcement
  are still open.

## State (HEAD f76ab50)

- Commits: 3fb14fc (research), 70cad9c (round one), 3e9dbca (graph evidence), 8f3a16c (round
  two), f76ab50 (merge of origin/main 713af05).
- `make check` green: 13921 passed, 14 skipped, 23 xfailed.
- Baselines for the next gate:
  - **23** `[imgui-error]` lines, from tests that exercise error recovery;
  - **1952** for `make typecheck-all` (red before this branch; don't let it rise).
- Built:
  - peer messages (H+, `peer-posted`, keyed on link id);
  - decisions in the asker's log, and channel decisions;
  - provenance on declarations;
  - the per-link channel log at `config_dir()/channels/<name>/<link-id>/`, with the relink sweep;
  - `send_to_peer` / `read_peer_messages`;
  - the minimal Session ▸ Link leads / Close channel, and the card line.
- Not built:
  - **round three:** delivery-first wakes (Decided 10), the 15-minute surfacing (Decided 11),
    waking the peer on channel decisions, and an operator surface for messages held on a closed
    link;
  - **round four:** the DIGEST thread, channel in NEEDS YOU/DETAIL, and the display check.
- Never seen on a display, never run between two live sessions.

## Process lessons (not recorded elsewhere)

- **Board.**
  - Always `claim_task(task_id=...)`: `claim_task()` gives the oldest claimable task whatever
    the role.
  - Spawn an agent only for a task that's claimable now, and tell it to release and report if
    refused.
  - Declare a fix task with touches limited to the files it needs. A fix whose touches reach a
    later task's files waits on that task, and that has deadlocked the board twice; only the
    operator can close a stuck task.
  - Use main's new `declare_task` dry run before declaring.
- **Builders mutate only scratch copies** (`cp -a` under `$TMPDIR`), never the tree. Two
  incidents in round one came from mutation checks run in the shared tree. Every spec says so.
- **Extras.** When a builder asks to write outside its touches, check the board for an
  unfinished task on that file before approving, and have it list the file as an approved extra.
  Gates check changed files against touches plus extras.
- **Interpreter.**
  - Run `make` from the worktree (composite `.venv`, borrowing the main checkout's packages).
  - A script needs `PYTHONPATH=<worktree>` or it imports the main checkout's `pptmstr`.
  - pytest must NOT have `PYTHONPATH`: `test_typeset_deps` breaks.
  - mypy ignores `PYTHONPATH`.
- **`make shot-xvfb`** can't keep an image outside the tree (its namespace tmpfs hides `$TMPDIR`),
  so a gate's shot step proves only that a frame rendered.
- **Never `git add -A`.** The worktree root holds 0-byte sandbox placeholder files (.bashrc,
  .gitconfig, .mcp.json, .claude/ …). Stage by path.
- **Review cadence that worked.** Builder, then reviewer (no shell; findings by reading), then a
  narrow fix task, then a short review, then the gate. Each pass found smaller problems. Skip the
  re-review only for wording-only fixes.
- **The log format only grows, and one malformed record ends a log for every record after it**
  (run). Treat every new record field or kind as permanent at review time.
- **Each new `Intent`/`Effect` variant adds `typecheck-all` errors** at existing `union.attr` test
  sites. Cast those lines; that clears old errors on them too.
