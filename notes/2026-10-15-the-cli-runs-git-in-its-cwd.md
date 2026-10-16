# Does the Claude CLI run a tree's git configuration when started in it?

## The question

reviewer-10 (rv-final, finding 1) and reviewer-8 (rv-wt-fix-3, finding 1), from a
claude-code docs issue citing the 2.1.78 changelog, say the CLI builds a git-status
section of its system prompt by running `git status` in its own working directory, and
that the CLI process is not inside pptmstr's Bash-only sandbox. If true, launching a
session in a tree a prior autonomous session wrote — the launcher's "continue there"
offer, or resuming a session in its own worktree — runs that tree's git configuration
as the operator. pptmstr sets neither `CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS` nor
`includeGitInstructions` (both reviewers grepped), so nothing suppresses it.

This is the execution path the launcher's own pre-press check was moved *after* the
press to avoid (`planning/2026-10-15-ui-rework.md`, round one item 8). Measured before
any fix is chosen.

## The method

`scripts/probe_cli_git_status.py`. It builds, under `$TMPDIR`, reviewer-8's decoy:

- `R`, a throwaway repo with one commit.
- `P`, a linked worktree of `R` (`git worktree add`), so `R` still lists `P` while
  `P/.git` is re-aimed — the launcher's listing is "not evidence" (worktree.py).
- `P/.decoy`, an independent git directory. `P/.git` is overwritten to read
  `gitdir: …/.decoy/.git`, so any git run with cwd `P` uses the decoy's config and
  treats `P` as its working tree.
- A tracked file routed through `filter.x.clean`, committed before the filter is
  defined, then made stat-dirty (`os.utime`) so `git status` must re-read it and run
  the clean filter. Separately, `core.fsmonitor` names a hook program git invokes on
  status. Both the filter and the fsmonitor program `touch` a marker.

The marker is the result; the model's narration is never read or printed (STYLE.md §2).
Four arms, each on its own fresh fixture:

- **a-default** — the CLI as pptmstr launches it today.
- **b-disable-env** — `CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS=1`.
- **c-settings-false** — `{"includeGitInstructions": false}` via `--settings`.
- **d-control-no-cli** — decoy installed, `true` run instead of the CLI; must leave no
  marker.

The CLI arms (a, b, c) need credentials and network that pptmstr's sandbox denies
(`/home/wreel/.claude/.credentials.json` is denied to a sandboxed command), so they are
run by the operator outside the sandbox with:

    .venv/bin/python scripts/probe_cli_git_status.py

The builder ran `--fixture-only` inside the sandbox: the control arm, plus a self-check
that runs plain `git status` in `P` and confirms the marker — proof the decoy is live.

## Results

### Builder-run, inside the sandbox (CLI version 2.1.301)

- **self-check (plain `git status` in P)** — exit 0, filter marker: **YES**,
  fsmonitor marker: **YES**. The decoy fires on a real `git status`; both channels work.
- **d-control-no-cli** — exit 0, filter marker: **no**, fsmonitor marker: **no**. The
  fixture does not fire on its own.

### Operator-run, outside the sandbox (2026-10-16, command cmd-20261016T015757Z-3d6fe79b71b1)

Run by the operator exactly as asked, `.venv/bin/python scripts/probe_cli_git_status.py`,
exit 0 in 10.6 s. The CLI was the SDK's bundled binary, version 2.1.301
(`.venv/.../claude_agent_sdk/_bundled/claude`), which is what pptmstr launches.

| arm | exit | filter | fsmonitor |
|---|---|---|---|
| self-check (git status) | 0 | YES | YES |
| d-control-no-cli | 0 | no | no |
| **a-default** | 0 | **YES** | no |
| b-disable-env | 0 | no | no |
| c-settings-false | 0 | no | no |

**The claim is confirmed.** Started in a tree whose `.git` names a decoy repository, the
CLI ran the decoy's configured clean filter as the operator, outside any sandbox, before
the model ran a turn. It did not run the decoy's fsmonitor, so its git call disables or
bypasses that channel and not the filter. Either documented switch stops it:
`CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS=1` in the environment, or `includeGitInstructions:
false` in settings. builder-20's caveat about a "no" in arm a does not apply, because arm a
answered yes.

## What each outcome would mean

- **a-default YES** confirms the claim: the CLI runs the tree's git config, unsandboxed,
  at launch. The "continue there" / resume-in-worktree paths then run a session-forged
  tree's program as the operator.
- **a-default no** refutes it: the CLI does not run `git status` (or does not run it in a
  way that triggers the filter/fsmonitor) at launch, and the exposure, as stated, is not
  real for this CLI version.
- **b / c YES while a is YES** means that suppression knob does not actually suppress the
  git run; **no** means it does.

The measurement names the cause and two switches that remove it. What switching it off
costs: a session's system prompt loses its git-status section (branch, recent commits,
working-tree status). An agent can still run `git status` itself, inside its own sandbox
where one applies. Choosing the scope — every session, or only sessions launched into a
worktree or a directory a session may have written — is the operator's call.
