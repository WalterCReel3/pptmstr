# Does the CLI start root turns that nobody sent?

2026-10-16. Task `unprompted-turn-probe`, which was raised by rv-tool-block-fix finding 2.
Script: `scripts/probe_unprompted_turn.py`. Test: `tests/test_probe_unprompted_turn.py`.

**Status.** The operator deferred the live runs to a session with more safeguards, in
these words: "Don't do that again. Make notes of adversarial testing you wish to do and
that will be done in a different session with more safeguards put up." That instruction
reached the task, as spec v2, after one run had already happened. That run's results are
recorded below as what was seen and must not be read as the complete plan. **The plan
that follows is what the safeguarded session runs.** Nothing more is to be run from a
session like this one.

## Plan for the safeguarded session

### The questions

1. Does the CLI start a root turn with no `query`? The answer seen once per trigger was
   yes, with n=1 for bg-bash and n=1 for bg-agent. The open parts are Monitor, more n,
   and other models.
2. Does each queued prompt get its own ResultMessage? A prompt is queued when it is sent
   while a turn is running.
3. What does `interrupt()` do when a prompt is queued: which results arrive, with what
   `terminal_reason`, and does the queued prompt still run?

### Arms and controls

Each arm runs on a fresh `ClaudeSDKClient` in a fresh scratch cwd. Every query is logged
before `client.query` is awaited, and every message is logged as `receive_messages`
yields it. `classify` pairs results with queries and flags injected or unprompted turns.

| arm | what it does | positive control for its "no" | expected | worst case |
|---|---|---|---|---|
| control | one prompted turn, no tools | is itself the control: must classify as 1 query and 1 prompted result | ~10 s | ~2 min |
| bg-bash | Bash `run_in_background`: `sleep 15; touch <cwd>/bg-bash.done` | marker seen while listening, and a follow-up prompted turn after a 45 s grace period classifies as prompted | ~70 s (seen: 67 s) | ~6 min |
| bg-agent | Agent `run_in_background`; the sub-agent runs `sleep 15 && touch <cwd>/bg-agent.done` | as bg-bash | ~75 s (seen: 73 s) | ~7 min |
| monitor | Monitor over `sleep 8; echo TICK; sleep 8; echo TOCK; touch <cwd>/monitor.done` | as bg-bash; an inconclusive result if Monitor is never called | ~70 s (never finished) | ~6 min |
| queued | Bash in the foreground (`sleep 12; touch queued-1.done`); once its PreToolUse fires, a second query runs `touch queued-2.done` | both markers say what ran, independently of the model's text | ~35 s | ~4 min |
| interrupt | as queued, then `interrupt()` 1 s after the second query, then a 30 s wait and a follow-up | `interrupt-2.done` says whether the queued prompt ran; the follow-up shows the reader is alive | ~50 s | ~5 min |

One round of all six arms is expected to take about 5.5 min. `--repeat N` multiplies
that, so `--repeat 3` is about 17 min. pptmstr's `request_command` kills a command after
10 minutes, so a whole round does not fit in it more than once. Split by `--arms`.

### Paths each arm writes

- `<base>/<arm>-XXXXXXXX/cwd/`: the CLI's cwd and the arm's markers (`bg-bash.done`,
  `bg-agent.done`, `monitor.done`, `queued-1.done`, `queued-2.done`, `interrupt-1.done`,
  `interrupt-2.done`).
- `<base>/<arm>-XXXXXXXX/config/`: the scratch `CLAUDE_CONFIG_DIR`. The CLI's own
  config, session transcripts and sub-agent transcripts go here. Under `--operator-login`
  the directory is created and stays empty.
- `<base>/<arm>-XXXXXXXX/events.jsonl`: the arm's log. It is written only when the arm
  ends, so an arm that is killed leaves none.
- `<base>` defaults to `mkdtemp("unprompted-turn-")` under `$TMPDIR`, or `/tmp`.
- **Under `--operator-login`**, `CLAUDE_CONFIG_DIR` is unset:
  `~/.claude/projects/<slug of the cwd>/<session>.jsonl` and the sub-agent transcripts
  beside it, plus whatever else the CLI writes in `~/.claude` and `~/.claude.json` for a
  new project. That last part was not measured. The CLI also reads
  `~/.claude/settings.json`, so the operator's user-tier hooks fire in the probe's
  sessions.
- The background task's own output file. The CLI chooses where it goes
  (`TaskNotificationMessage.output_file`) and the probe does not log it. Unmeasured.
- Background processes (`sleep`) that outlive a cancelled run. For the cancelled run,
  pptmstr reported "2 process(es) started beside it could not be read".

### Command lines

Auth from the environment, which is the isolated default:

    ANTHROPIC_API_KEY=... .venv/bin/python -u scripts/probe_unprompted_turn.py \
        --arms control,queued,interrupt --base /tmp/unprompted-turn-A
    ANTHROPIC_API_KEY=... .venv/bin/python -u scripts/probe_unprompted_turn.py \
        --arms bg-bash,bg-agent,monitor --base /tmp/unprompted-turn-B

With the operator's stored login (not isolated from `~/.claude`, as described above),
add `--operator-login` to either line. Without a sandbox's API access, these are offline
and safe to run anywhere:

    .venv/bin/python scripts/probe_unprompted_turn.py --self-check
    .venv/bin/python scripts/probe_unprompted_turn.py --dry-run [--operator-login]
    .venv/bin/python -m pytest tests/test_probe_unprompted_turn.py

### Attempts so far

- cmd-20261016T152557Z-1cd0cc366128: refused by the script itself in 0.6 s. The
  operator's shell had neither `ANTHROPIC_API_KEY` nor `CLAUDE_CODE_OAUTH_TOKEN`.
- cmd-20261016T152917Z-2f51f4b8dee2 (`--operator-login --repeat 3 --base
  /tmp/unprompted-turn-probe`): the operator cancelled it during the monitor arm of round
  1. pptmstr's record of it holds no output, because its stdout went to
  `/tmp/unprompted-turn-probe.out`. That file and the three finished arms' `events.jsonl`
  are what the results below are read from.
- cmd-20261016T153949Z-fde93eeaf75b (queued, interrupt, bg-bash and monitor only): the
  operator refused it, saying "I'm still refusing probes", and nothing ran. The request
  was made after the lead had posted a stop, which the builder had not yet read. That is
  recorded as an incident on the task.

# Partial data on disk, from the cancelled run

This section is partial, unreviewed and n=1. It lists what
`/tmp/unprompted-turn-probe/` holds: `control-*`, `bg-bash-*` and `bg-agent-*`, each with
an `events.jsonl`, and `monitor-*` with no log. It is not the plan's answer, and the
safeguarded session is to confirm or refute it. The headings below describe those logs.

## What the logs contain

In the bg-bash and bg-agent logs, the prompted turn ended and then the background work it
started finished. CLI 2.1.301 then ran a root turn of its own. That turn has its own stream, its own
AssistantMessages and its own ResultMessage, and no `query` asked for it. The
ResultMessage carries `origin={"kind": "task-notification"}`. It was seen for both
triggers that were run:

| arm | n | prompted turn ended | background work done | turn nobody sent starts | its result | origin |
|---|---|---|---|---|---|---|
| bg-bash (Bash `run_in_background`) | 1 | 5.9 s | 19.5 s (`task_notification`) | 20.9 s (`message_start`) | 25.4 s | task-notification |
| bg-agent (Agent `run_in_background`) | 1 | 4.0 s | 27.3 s (SubagentStop, then `task_notification`) | 28.4 s | 31.9 s | task-notification |

Times are seconds since the arm started. If the safeguarded session confirms this, finding
2's premise holds. pptmstr opens a turn only on `send`'s SYSTEM line, which means each of these turns runs with no open turn
in the transcript, and per the finding its tool calls would read NO_RESULT while they run.
These two turns made no tool calls (the model only acknowledged the notification). That
outcome is the model's choice, and the CLI does not guarantee it.

A turn that was seen needs no positive control. The controls exist to keep
a "no" honest, and they ran anyway:

- **control arm (n=1):** a prompted turn recorded through the same reader classified as 1
  query and 1 prompted result, with nothing flagged.
- **follow-up in both background arms:** a prompted turn sent about 40 s after the
  injected one classified as prompted, so the reader was still recording after the window.

## What the wire looks like, for whoever fixes it

Read from the two recorded logs. This is n=1 per trigger, so it describes this build and
promises nothing about others.

- The CLI emits nothing before the injected turn to say it is coming, apart from the task
  ending. In order it emits `TaskUpdatedMessage(status=completed)`,
  `TaskNotificationMessage`, `SystemMessage(background_tasks_changed)`, then
  `SystemMessage(init)` and `SystemMessage(status)`, and then the stream's `message_start`.
- **`SystemMessage(init)` comes before every turn**: the first prompted turn, the injected
  turn and the follow-up. Its arrival is a candidate "a turn begins" signal that does not
  depend on `send`. One `init` per turn was seen in every turn of all three logs. That is a
  reading of n=3 logs, not a documented contract.
- **No root `UserMessage` carries the injected prompt.** The SDK does not pass
  `--replay-user-messages`, so `UserMessage.origin` never arrives for it. The only origin
  seen is on the ResultMessage, at the turn's end. A fix that keys on origin can therefore
  classify the turn only after it is over.
- Sub-agent messages (`parent_tool_use_id` set) kept arriving after the parent's result,
  as the driver already expects. They are not root turns and the classifier does not count
  them.

## Not measured

- **Monitor.** The arm was running when the operator cancelled the command
  (cmd-20261016T152917Z-2f51f4b8dee2), and it left no log. Whether Monitor events start
  turns is unknown.
- **One ResultMessage per queued prompt (`queued` arm)** and **an interrupt with a prompt
  queued (`interrupt` arm)**: neither ran. The first command was cancelled before reaching
  them. A second, smaller command (cmd-20261016T153949Z-fde93eeaf75b) asked for these two,
  and the operator refused it without giving a reason. Both turn-boundary risks the review
  named are still open.
- n is 1 per arm. The round was meant to run three times.
- One model (claude-sonnet-5), one machine, and the bundled CLI 2.1.301 (claude-agent-sdk
  0.2.168).

## How it was run

- Options are `AgentSession._options()` for a session in a scratch cwd under `/tmp`. Three
  things were changed, and the script's docstring gives the reason for each. Hooks were
  replaced by recording hooks: they allow every tool except the bus, which they deny,
  because its store is not running. stderr went to a list. `CLAUDE_CONFIG_DIR` was set or
  left unset as described in the next point.
- **`--operator-login`.** The spec said "never the operator's ~/.claude". The probe's
  default sets a scratch `CLAUDE_CONFIG_DIR`, so it needs auth in the environment. The
  operator's shell has none, and the first command (cmd-20261016T152557Z-1cd0cc366128)
  refused in 0.6 s. The run behind these results used the opt-in `--operator-login` flag,
  which leaves `CLAUDE_CONFIG_DIR` unset. The CLI therefore read the operator's user
  settings and wrote session transcripts under `~/.claude/projects/` for the `/tmp` cwds.
  The flag was named on the command line the operator read before running it. The
  departure was posted to lead.
- The command: `.venv/bin/python -u scripts/probe_unprompted_turn.py --operator-login
  --repeat 3 --base /tmp/unprompted-turn-probe`. It ran the control, bg-bash and bg-agent
  arms of round 1 and was cancelled in monitor. The logs are in
  `/tmp/unprompted-turn-probe/*/events.jsonl` on the operator's machine. The bg-bash log is
  embedded in the test as `RECORDED_BG_BASH`, with its 15 `thinking_tokens` frames removed.

## Classifier

`classify` pairs root ResultMessages with queries in order. A result whose origin is
anything but human or absent counts as injected, whatever the count says. Root activity
(an AssistantMessage, a root UserMessage, or a stream `message_start`) with no query
outstanding starts an unprompted turn. The tests cover each case from SDK message objects
through `describe`, plus two arms driven over a fake client through the live reader. Seven
mutations of `classify`/`verdict`/`describe` were each killed by at least one test.
