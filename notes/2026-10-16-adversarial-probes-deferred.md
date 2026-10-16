# Adversarial and live-CLI probes deferred to a safeguarded session

**Dated:** 2026-10-16 · **Status:** a list of work, none of it to run in the ui-rework session.

The operator, after cancelling a live probe that was running too long under their own login:
"Don't do that again. Make notes of adversarial testing you wish to do and that will be done in
a different session with more safeguards put up."

So the ui-rework session asks the operator to run no live CLI probe and no adversarial probe.
The scripts below are built and their offline tests run. What follows is what each would measure,
and why it was wanted.

## Safeguards the running session should have (the lead's proposal; not decided)

- **Credentials:** its own auth (a throwaway `CLAUDE_CODE_OAUTH_TOKEN` or API key) and its own
  `CLAUDE_CONFIG_DIR`. The operator's `~/.claude` is never read or written. The
  `--operator-login` flag on `scripts/probe_unprompted_turn.py` exists only because the scratch
  default could not authenticate, and it must not be used.
- **Isolation:** a container or VM whose only network route is the API, with no access to the
  operator's home.
- **Time and processes:** a stated wall-clock budget per arm and a hard timeout that kills the
  CLI's whole process tree. Each arm's result is written as soon as the arm finishes, so a
  cancelled run still keeps what it measured. When cmd-20261016T152917Z-2f51f4b8dee2 was
  cancelled, pptmstr recorded two processes beside it that it could not read.
- **Controls:** a positive control in every arm, as the trust probe's e0 had. A "no" without
  one is not reported.

## The probes

1. **Does the CLI start a root turn nobody sent?** `scripts/probe_unprompted_turn.py`, task
   unprompted-turn-probe. Its plan, arms and command lines are in
   `notes/2026-10-16-does-the-cli-start-turns-unprompted.md`. Why: turn-boundary opens a turn only
   on a send (a SYSTEM line, and NOTICE once wake-asker lands). If the CLI starts turns on its
   own, for example after background Bash or a sub-agent finishes, that turn's running calls read
   NO_RESULT (rv-tool-block-fix, finding 2: consequence run, premise unmeasured). The same probe
   covers turn-boundary's two open risks: one ResultMessage per queued prompt, and an interrupt
   while a prompt is queued. Attempts so far: cmd-20261016T152557Z-1cd0cc366128 refused for lack
   of auth. cmd-20261016T152917Z-2f51f4b8dee2 ran with `--operator-login`, which wrote session
   transcripts under the operator's `~/.claude/projects`, and was cancelled by the operator in
   its fourth arm. Its output went to `/tmp/unprompted-turn-probe.out`: the background Bash and
   background Agent arms each saw an unsent turn (n=1, unreviewed). cmd-20261016T153949Z-fde93eeaf75b
   was refused by the operator. Monitor, queued prompts and interrupt-with-queue have never run.
2. **The wake actually wakes.** Task wake-asker is built against unit tests only. Live
   questions: does a send into an AWAITING_INPUT session start a turn on the current CLI build?
   Does a burst of answers coalesce into one turn? Is the NOTICE line drawn as pptmstr's in a
   real session? Not run. The wake, and NOTICE's turn accounting, rely on two things measured
   only at n=2 on CLI 2.1.226: a query sent into AWAITING_INPUT starts a turn, and a query sent
   mid-turn runs as its own turn with its own ResultMessage. builder-56's proposed probe is in
   wake-asker's not-run.
2a. **A cancelled send** (turn-consistency): can a cancellation arrive after the CLI has read
   the prompt? If it can, the send withdraws its turn while the CLI still answers it.
3. **User-only setting sources** (`probe_cli_git_status.py --user-only`, the u0…u-user-tier-hook
   arms in `notes/2026-10-15-the-cli-runs-git-in-its-cwd.md`). Never run. The operator chose that
   nothing changes about settings loading (dq-5827ae4642b7), so this matters only if that choice
   is revisited.
4. **Protected paths, again on each CLI build** (`scripts/verify_gate_hook_write.py`). On CLI
   2.1.301 every route measured was DENIED by the CLI after pptmstr's gate allowed it, at n = 1
   per arm. Still open: whether the sandbox or the permission mode refused the a5 Bash writes,
   and whether it holds at n > 1 and across models.
5. **The trust probe at n > 1** (`probe_cli_git_status.py --pptmstr --trust`). It was run once,
   and every YES row is n = 1.

## Not in this list

Unit tests and offline fixture checks keep running in the ui-rework session as normal. The rule
covers only probes that start a live CLI, or that aim at the CLI's or pptmstr's protections.
