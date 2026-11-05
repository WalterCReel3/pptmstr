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

## State (after round three)

- Commits: 1513a23 (research), a6b0f51 (round one), 944c207 (graph evidence), 720b406 (round
  two), 88af369 (merge of origin/main 713af05), 83f31b0 and 769c898 (this note), 91d4426 (round
  three), 982e20d (coordinator concept), then the merge of origin/main 7d3a21f (d305c0b), the
  first three-lead case study, round four's case-study items, and the merge of origin/main
  d81db99 (171010e).
- `make check` green after that merge: 16081 passed, 76 skipped, 23 xfailed.
- Baselines for the next gate:
  - **23** `[imgui-error]` lines, from tests that exercise error recovery;
  - **1936** for `make typecheck-all` after the merge (main alone 2067, the branch alone 1914; no
    error in the merged tree is absent from both sides). Don't let it rise.
- Built:
  - peer messages (H+, `peer-posted`, keyed on link id);
  - decisions in the asker's log, and channel decisions;
  - provenance on declarations;
  - the per-link channel log at `config_dir()/channels/<name>/<link-id>/`, with the relink sweep;
  - `send_to_peer` / `read_peer_messages`;
  - the minimal Session ▸ Link leads / Close channel, and the card line.
  - round three: delivery-first wakes with the K = 6 backstop (Decided 10), the unanswered-question
    row (Decided 11), decision wakes, and held-mail rows. See "Decided during round three" in the
    build plan.
  - round four, from the first three-lead run: peer identity on every link surface, owed
    answers named at resume, sender-only withdrawal (`peer-retracted`), whose move with an action
    on every peer row, the link's thread in DETAIL, the sweep's own report type, the channel on
    decision rows, and one "edited" rule.
- Not built:
  - **left from round four:**
    - the display check (the operator's);
    - provenance in the board pane on AUTONOMOUS;
    - who closed a link (held until the cross-window case shows up);
    - cutting redundant relay of decision answers;
    - an operator control to withdraw a message;
    - the digest summary naming a decision's channel;
    - ConcernEdited's missing node check;
    - the reducer's per-member peer scans (2.7–5.2 ms a step at 2000 peer messages; this
      predates the branch's round four).
- Rounds two to four were never seen on a display. The first three-lead run (2026-10-24) used
  round three; nothing since has run between live sessions.

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
  - In an unattended session, the Write/Edit tools are bounded to the worktree, so a scratch
    copy and its scripts are made with Bash only (`cat > … <<EOF`, `python -c`).
  - `$TMPDIR` can be empty. `/tmp/claude-1000/…` is the sandbox-writable fallback.
  - Never write a file into the worktree to carry scratch work (2026-10-25, P-reducer-perf).
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
- **Commit timestamps** (operator, 2026-10-20; tightened 2026-10-24). Author and committer dates,
  and any other timestamp written, fall outside 09:00–17:30 ET on every day, weekends included,
  and at a sensible time (early morning or evening). The 2026-10-24 rule re-dated the first commit.
  Dates keep commit order. Set both `GIT_AUTHOR_DATE` and `GIT_COMMITTER_DATE` on every commit.
  Re-dating an existing commit uses `git commit-tree` on its tree, so the trees stay identical
  and a merge isn't re-resolved, then updates the hashes cited in notes.
- **Never `git add -A`.** The worktree root holds 0-byte sandbox placeholder files (.bashrc,
  .gitconfig, .mcp.json, .claude/ …). Stage by path.
- **Review cadence that worked.** Builder, then reviewer (no shell; findings by reading), then a
  narrow fix task, then a short review, then the gate. Each pass found smaller problems. Skip the
  re-review only for wording-only fixes.
- **The log format only grows, and one malformed record ends a log for every record after it**
  (run). Treat every new record field or kind as permanent at review time.
- **Each new `Intent`/`Effect` variant adds `typecheck-all` errors** at existing `union.attr` test
  sites. Cast those lines; that clears old errors on them too.
