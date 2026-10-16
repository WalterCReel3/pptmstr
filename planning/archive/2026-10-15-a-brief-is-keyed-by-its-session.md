# A brief is keyed by its session, not by the directory it ran in

**Dated:** 2026-10-15 · **Status:** built on branch `worktree-home` · **Found by:** the operator,
after an autonomous launch into a CLI-made worktree (`<repo>/.claude/worktrees/<name>`) was
refused, asking why `~/.pptmstr/worktrees/<repo>/<branch>` had been ruled out · **Changes:**
[`2026-08-17-a-session-premise-is-a-place-not-a-message.md`](2026-08-17-a-session-premise-is-a-place-not-a-message.md)
(where a brief lives),
[`../2026-10-07-a-session-can-run-in-its-own-worktree.md`](../2026-10-07-a-session-can-run-in-its-own-worktree.md)
D4 (rewritten)

## What was wrong

`brief.session_dir` named a brief `~/.claude/projects/<cwd slug>/briefs/<session id>/` so that it
sat beside the CLI's transcript. Its slug turned only `/` into `-`; the CLI turns every
non-alphanumeric. On any path with a `.` or `_` the brief was filed in a directory the CLI never
wrote. Known since 2026-09-21 (`t-record-slug-defect`), left unfixed, and then designed around:
the worktree record's D4 required "no dots anywhere in the path", which ruled out the
`~/.pptmstr/worktrees/…` convention for no reason but this defect.

Matching the CLI's slug would not have fixed it. Past 200 characters the CLI's directory name
carries a hash the CLI and the SDK compute differently (the SDK's own `_find_project_dir` says so
and falls back to prefix matching), and a resume runs in whatever directory it is launched from
(worktree record D3), so a name derived from the directory is a different name for the same
session.

## Decided (D)

The operator, 2026-10-15, offered `./.pptmstr` in the working tree; the alternative put to them
was pptmstr's own configuration directory, keyed by session id. "Nah, the config folder is a
valid choice. Let's go with that."

**`<config>/briefs/<session id>/`** (`brief.default_root`, `brief.session_dir`). The rest of
this section is the builder's reasoning under that decision.

- **Not in the working tree.** The 08-17 reason stands — every launch would leave an untracked
  directory — and a second one has appeared since: a contained session may write anywhere under
  its cwd, so its premises would be rewritable by the agent acting on them.
- **Not `sessions/<id>/brief/`.** That tree is denied whole to a contained `Bash` command
  (`sandbox.denied_records`, the session's own directory included), and `approval.records_read`
  denies one session another's directory there. A worker lists and reads its brief, and a fork
  is handed its parent's, so either denial cuts a team off from its premises. `briefs/` is its
  own root.
- **Readable, never writable, by a tool call.** `approval.records_written` treats `briefs` as a
  root (`_WRITE_ONLY_ROOTS`) on every rung; `records_read` does not. `sandbox.denied_record_writes`
  adds it to `filesystem.denyWrite`, because a session started in `$HOME` has the configuration
  directory inside its write region. Under `~/.claude/projects` the CLI's own protection of
  `.claude/**` did this; moving out of it without replacing that would have been a regression.
- **The old location is read, not moved.** `brief.existing_dir` answers for a resume: the
  id-keyed directory if it exists, else `legacy_session_dir(cwd, id)` under `~/.claude/projects`,
  the old formula exactly. Nothing migrates. On the operator's machine on 2026-10-15 there were
  about 360 brief directories at the old location, five of them under slugs the CLI never wrote
  (`adv-text-experiment` ×3, a `walter.reel` path, one CLI-made worktree). A legacy brief keeps
  the old limit — found only from the cwd it was written under.
- **`~/.claude` is still not denied wholesale** to a contained command, now because legacy briefs
  are still handed to resumed sessions.
- **`--fake` seeds under a fresh temporary directory** and looks for no existing brief, because
  that run writes nothing into the configuration directory (`test_under_the_fake_driver_nothing_is_moved_recalled_or_written`
  caught the seed landing there).

**Cost.** A brief no longer sits beside its transcript. The 08-17 record wanted "what did this
worker act on" answered from one directory; it is now two, joined by session id, which is what
every other pptmstr record is joined by.

## Verified

Full suite on 2026-10-15 in the `worktree-home` worktree before the test changes: 11331 passed, 7
failed, every failure an assertion of the old layout. After: the affected modules pass. New gate
tests were mutation-checked: dropping `_WRITE_ONLY_ROOTS` from `records_written` fails
`test_no_rung_admits_a_write_into_any_sessions_brief` on `AUTONOMOUS`.

**Not run:** a real team launch, a real resume of a legacy brief, and a contained session reading
its brief under the new `denyWrite` entry. The sandbox keys are unchanged in kind — one more path
in a list the CLI already applies — but that is a reading, not a measurement.
