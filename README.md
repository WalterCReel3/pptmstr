# pptmstr — Puppet Master

**Run a fleet of Claude agents. Hold every string.**

<!-- HERO: TRIAGE layout, full window, a frame composed to fill it — fleet rail
     with several healthy sessions and one team, a populated review queue, DETAIL
     showing an expanded diff. No failure state front and centre.
     ![The TRIAGE layout: fleet rail, review queue, detail pane](docs/images/hero.png) -->

A multi-agent orchestrator with an immediate-mode UI. It runs N Claude agent
sessions concurrently, keeps every one of them legible at a glance, and lets
nothing reach your disk that you did not read first.

Reasoning, cost, context health and the next tool call are on screen while they
happen. Every write parks in front of you as a diff — approve it, reject it with a
reason the model sees, or edit the arguments and approve the corrected call.

Built on the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview)
for agent execution and [Dear ImGui](https://github.com/pthom/imgui_bundle)
(via `imgui-bundle` / hello_imgui) for the UI.

---

## Nothing is hidden, on purpose

Most agent tools spend their design budget on looking effortless: a spinner, a
progress line, a diff at the end. That is the right call for a product that wants
to feel like magic and the wrong one for work you are accountable for. At minute
forty of a session going sideways, "working…" tells you nothing, and a diff that
lands at the end lands long after intervening would have been cheap.

pptmstr goes the other way. What the fleet is doing is on screen while it is doing
it:

- **Reasoning streams as it is produced**, at the root, rather than being
  reconstructed once the turn ends. It arrives as a *summary* — current models never
  return the raw chain of thought — and the pane says so instead of implying more.
- **Every mutating call is shown before it runs**, as a diff, with the arguments
  editable in place.
- **Context is a health signal, not a budget.** The question it answers is "is this
  session about to compact, and should I retire it?" — not "how much have I spent."
  Those are different axes and live in different widgets.
- **Sub-agents are a visible tree** inside their session's card, and a call made
  *inside* a sub-agent parks against that sub-agent rather than its parent.
- **Agent-to-agent messages pass the same gate as writes**, so a concern can be read,
  rejected with a reason, or edited before it is delivered.

Density is not the same as a wall of numbers, and the layouts carry that weight:
TRIAGE ranks what is waiting on you, FOCUS drops to a single session. Panes are
expected to say what they *cannot* show — sub-agent output does not stream, so the
transcript says so rather than letting a quiet row read as a stuck one.

The design premise is that **the operator is the bottleneck on purpose**, so the
interesting engineering is in making that bottleneck cheap. Approval is a runtime
state, not a prompt: an agent awaiting review is idle rather than spinning, and the
whole app drops to idle frame rate while it waits on you.

Non-goals: i18n, multi-user, remote access.

<!-- GATE: the review queue with one call expanded — an Edit diff, the reject and
     edit affordances visible, the keyboard hint line legible.
     ![A tool call parked in the review queue as a diff](docs/images/gate.png) -->

## What works today

The build order in [orchestrator-design.md](orchestrator-design.md) §8 is finished:

| Step | | |
|---|---|---|
| 1 | Store, snapshot, intents, Bridge | **done, tested** |
| 2 | Theme layer + agent tree pane against a fake driver | **done, tested** |
| 3 | One real agent over the SDK | **done, tested** |
| 4 | The approval gate | **done, tested** |
| 5 | Idling | **done, measured** |
| 6 | Concurrency, sub-agent tree, review queue, batching | **done, tested** |
| 7 | Transcript pane | **done, tested** |
| 8 | Work templates + inter-agent message bus | **done, tested, verified live** |

What comes next comes from using the thing, and lives in [planning/](planning/) as
dated scope snapshots. Currently open, roughly in the order they cost something:

| | |
|---|---|
| [`needs_you` sorts two clocks](planning/2026-08-12-needs-you-sorts-two-different-clocks.md) | the inbox is not oldest-first; approvals always sort last |
| [The README shows nothing](planning/2026-08-13-the-readme-cannot-show-what-it-sells.md) | captures are not reproducible, so the screenshots above are placeholders |
| [A halt does not reach queued work](planning/2026-08-14-a-halt-has-to-reach-work-that-has-not-started-yet.md) | closing a queued session starts it anyway, and it is then unreachable |

Read [STYLE.md](STYLE.md) before adding a record, an intent, or a projection.

## Getting started

```sh
./bootstrap.sh          # probe -> venv -> install -> window smoke test
make check              # lint + typecheck + tests
make run                # launch

.venv/bin/python -m pptmstr                     # start empty, launch from the UI
.venv/bin/python -m pptmstr --task "..." --cwd ~/some/project
.venv/bin/python -m pptmstr --task a --task b --cap 2   # concurrent, bounded
.venv/bin/python -m pptmstr --fake              # UI only, no SDK, no cost
```

Reads auto-approve. Everything that writes, runs a shell, reaches the network or
spawns a sub-agent parks in the review queue until you answer it — as does any tool
this build has never heard of. With no operator attached the gate denies rather than
hanging.

Start a session with `Ctrl+N`, or **Session ▸ New Task…**, from either layout. Each
carries its own working directory and model, so one window can drive work across
several projects. `Ctrl+Enter` launches; `Enter` breaks a line in the task box;
`Esc` dismisses without discarding the draft. The fields scroll and the **launch** and
**cancel** buttons stay under them, on screen, however long the body grows.

Under each control is at most one line. The explanations are behind a small **(?)**
beside the line they explain: it opens a window over the dialog with the whole of
it, which `Esc` or **close** puts away and leaves the dialog as it was. What is on
screen without asking is the line under the gate — what the rung adds, or, on
`autonomous`, a red band saying the session will write files and run commands
without asking — and a line for each thing that changes what the button does: a
rung refused for the directory, a held launch and why in a few words, where a
picked session runs.

**Resume an existing session instead**, under the working directory, lists the
sessions the CLI recorded for that directory and its git worktrees. Picking one keeps
New Task open and makes it a resume: the task box is the message the session is sent,
and the session runs in the directory it was recorded in, which the line under the
list names when it is not the working directory above. Picking it fills the model,
team and sub-agent cap from the session's own record where it has one; they stay
yours to change, and a session with no record leaves them as they were. The gate is
the one on New Task's dial. A gate the session's directory is refused for holds the resume, where a new session
would be started under `strict`; *launch fresh instead* undoes the pick. A resume that
starts nothing comes back to New Task with the message and the pick.

The review loop is keyboard-driven: `j`/`k` to move, `a` approve, `r` reject with a
reason the agent sees, `e` edit the arguments and approve the corrected call,
`Shift+A` approve everything from the selected agent, and `m` for the controls of the
session the cursor is on. Approving the whole queue
across every agent exists but costs a second click that states the count — it is the
one action here that can write something nobody read.

### Sessions are conversations, not one-shot runs

A finished turn leaves the session connected and marks it `YOUR TURN` — send another
prompt, interrupt the current turn, or close the session to reclaim its subprocess.
An agent that asks a question is therefore something you can answer rather than
something that looks finished.

<!-- CONVERSATION: FOCUS layout on the AWAITING_INPUT session — the transcript
     ending in the agent's question, the prompt box below it.
     ![An agent asks a question and waits for an answer](docs/images/conversation.png) -->

Every prompt box in the application sends on `Ctrl+Enter` and takes `Enter` as a
newline — a prompt is routinely several lines, and the key that would send it
half-written is the one pressed most often.

The transcript pane styles output by kind — reasoning, output, tool calls, results,
errors, compaction boundaries — with toggles for reasoning, wrapping and follow-tail,
plus a filter. Concurrency is bounded by `concurrency_cap`; over-cap sessions queue
rather than being refused, and the pool is shown in the status bar.

## Teams

A session can run as a **team**: a lead plus named worker roles, chosen in the
launcher or with `--template`. `solo` is the default and behaves exactly as a session
did before teams existed.

| | |
|---|---|
| `solo` | one agent, no team |
| `feature` | lead plans, `builder` implements, read-only `reviewer` attacks it |
| `research` | coordinator frames, `investigator` builds the case, `skeptic` refutes it |

Agents coordinate over an **in-process message bus** rather than the harness's own
channel, because that is what makes a message reviewable: `post_concern` parks in the
review queue like any write, so a concern can be read, rejected with a reason, or
edited before it is delivered. A shared task board carries dependency edges — blocked
work becomes claimable the moment its dependencies complete, and workers claim rather
than being assigned.

The sender on a concern is **stamped by the approval gate**, never taken from the
tool's arguments. An in-process MCP handler is told only the tool name and its
arguments — no session, no agent — so a sender the model wrote would be a sender the
model chose. The gate is the only participant that knows, which makes it the bus's
authentication layer as well as its review point.

Review roles are given read-only tools on purpose. A reviewer that can quietly fix
what it was asked to find stops reporting it.

### The board is written to disk, and nothing deletes it

A team's board is logged as it changes, and a session that is **resumed** gets its
board back before its lead's first bus call — the tasks, who held them, and the
concerns, not only the lead's memory of them.

- **Where.** `~/.config/pptmstr/sessions/<session id>/board/`, one small file per
  board fact, beside that session's `session.json` (below). `XDG_CONFIG_HOME` is
  honoured when it is absolute, as it is for `settings.json`.
- **Logs from before a session had a directory** were kept at
  `~/.config/pptmstr/boards/<session id>/`. They are moved when pptmstr starts with
  board logging on — one rename each, before the window opens, never under `--fake` —
  and a symbolic link to the new place is left at the old name, so that a pptmstr that
  still appends by the old name (an older build left running, or started later) writes
  into the same log and not a second one. Nothing is merged and nothing is deleted. A
  log that could not be moved is left where it was: it is read from there when its
  session is resumed, and never appended to, there or at the new path.
- **Two histories.** A session with records under both `boards/<id>/` and
  `sessions/<id>/board/` has two logs that each hold part of its board: something
  appended by the old name after the move — in the moment between the rename and the
  link, or because the link could not be left. The old directory then holds the later
  facts and the new one the earlier, and neither is the whole board. pptmstr restores
  the one under `sessions/`, names both, and runs the session **unrecorded** until one
  history is left. To resolve it, with pptmstr closed: read both (each record is one
  small JSON file, numbered in the order it was written), keep the directory that is
  the board, and move the other out of both `boards/` and `sessions/`. If the one you
  kept is under `boards/`, move it to `sessions/<id>/board` afterwards. A log that was
  only left at the old path is resolved the same way: `mv boards/<id>
  sessions/<id>/board`. Each of these is said in the log once at startup, counted in
  the status bar for the rest of the run, and given with its reason on the BOARD pane
  of a session it affects.
- **What is in it.** Every specification, result, not-run list and concern body the
  session's board has held, as plain text — including the body of a concern that was
  later withdrawn or edited, which the board itself stops showing. Not only prose:
  the path of every approved write that was measured against a task, whether or not
  the task declared it — file names from your tree; the session id and each
  sub-agent's id beside the name it went by; and the wall-clock time of every act.
- **Closing a task mails its holder.** `close...` on an unfinished row of the BOARD
  pane ends the task as closed by the operator, with the reason you type as its
  result. If an agent holds it, the same reason is filed as a concern from the
  operator to that agent, and both the close and the concern are in the log. The
  agent is not stopped: it reads the reason when it next reads its inbox, and is
  told the task was closed if it tries to finish or release it.
- **What is not in it.** Decisions raised for the operator, and the answers to them.
  A resume brings the board back and no decision: one you answered that its asker had
  not yet read is gone when pptmstr exits, and nothing on screen says so.
- **How long.** Until you delete it. Nothing in pptmstr removes a log, so it outlives
  the CLI's own transcript of the session, which the CLI clears on its retention
  schedule (`cleanupPeriodDays`). Delete a session's `board/` directory only while no pptmstr
  window that has run that session is open; it then resumes with an empty board. A
  window that is still open goes on appending to the emptied directory, and that log
  holds only what happened after the deletion: it restores short wherever a record is
  about a task or concern the deleted files introduced.
- **Off.** Set `"board_log": false` in `~/.config/pptmstr/settings.json`. It is read
  when the application starts and has no menu entry: with it off nothing is read,
  written or created. `--fake` never logs. The switch fails towards off: if that
  file is there and cannot be read as settings — a trailing comma is enough — or
  `board_log` is anything but a bare `true` or `false` (`"false"` in quotes
  included), nothing is recorded for that run. So is a key that is `board_log`
  misspelled: `board-log`, `boardlog`, `Board_Log` — the same letters once case and
  punctuation are taken out — switches recording **off**, whatever it is set to and
  whatever a correctly spelled key beside it says. Any other setting that cannot be
  read falls back to its default; this one is on by default, so it does not. Only a
  missing file, or a file with no key of either kind, means on. In each of these
  cases the BOARD pane says `not recorded:` with which of the three it was and the
  file's path, and settings changed in that run are not saved — the file is left as
  it is until it is repaired and pptmstr restarted.

The BOARD pane says which of these holds for the session under the cursor: where it
is recorded, that it was restored and from a log written up to when, and — in the
warning colour — when a restore came back short, or the session is running
unrecorded when nobody chose that, and why. Recording you switched off, and `--fake`,
are said in the ordinary text colour. A restore that is short in any way (a damaged log, an id another
session already holds, a log whose writer had stopped) is shown as short and is
**not appended to** by the window that restored it, because a record written behind
a short log would hide that it is one.

That is for good, and not only for that window. What made a log short is still in it
the next time it is read, so every later resume of the session restores the same
prefix, runs unrecorded — the pane says so — and loses that run's board when pptmstr
exits. The way out is the one above: with no window that has run the session open,
delete its `board/` directory, and the session resumes with an empty board that is
recorded again.

### The fleet comes back closed, from one file per session

Every session pptmstr has run gets a directory, `~/.config/pptmstr/sessions/<session
id>/`, holding `session.json` and, for a team, the `board/` log above. When pptmstr
starts, each session whose file says it is on the fleet is put back in the FLEET pane
as a **closed card**: its title, how it ended and how long ago. A closed card is a
card and nothing else. No process is started for it, nothing is spent, its board log
is not read and its board is not shown until you resume it; the status bar counts it
apart, as `N agents · M closed`.

- **What is in `session.json`.** What the session was launched with and what became of
  it: the first task text (the card's title), the last message you sent it, its model,
  directory, team, brief directory, sub-agent cap, the gate as last granted and whether
  it was contained; when it was first and last launched, last active, and how it ended
  (`closed`, `failed`, or `shutdown` when pptmstr quit with it running — no end at all
  means pptmstr was killed with it live); an estimate of its cost; and whether it is on
  the fleet. Launch text and messages are in it as plain text.
- **When it is written.** Only for a session that really started: the file is created
  when the session's CLI has connected and been handed its opening text, so a launch
  that is refused, never connects, or cannot be given its text leaves none, and a resume that is refused leaves the file's gate and last message as
  they were. The cost is an estimate and may double-count across resumes: whether a
  resumed CLI reports its cost from zero has not been measured. `--fake` writes none and recalls none. Keys this build does not know
  are kept as they are; a file that cannot be read, or that a newer build wrote, is
  never replaced — its session has no card, and the log and the status bar say so.
- **It is never evidence that a session exists.** The CLI's transcripts decide that. A
  file whose transcript the CLI no longer has draws as `transcript gone`, and can only
  be removed from the fleet.
- **Nothing deletes it.** Removing a session from the fleet sets a key in its file and
  touches nothing else. The directory, the file and the board log stay until you
  delete them, with pptmstr closed.
- **Two windows** both start with the same closed fleet, and nothing yet stops both
  resuming one session. A known issue, to be fixed before this is called beta.

**A card's controls are on the card.** Right-click it, or press `m` with the cursor
on it (also **Session ▸ Controls for the focused card…**); arrows move, `Enter`
chooses, `Esc` closes. The menu is labels: an item that cannot act is greyed with a
few words of why beside it, and nothing is explained under an item.

- **Resume…** opens a short form of its own, not New Task, and a card is the only
  thing that opens it: the session, its directory
  and its team are shown as facts and cannot be changed; you choose the message, the
  gate, the model and the sub-agent cap. The message box opens with the last message
  you sent that session, and a message is required. The gate opens on `strict`
  whatever the session last ran under. The session keeps its title — the message is
  sent to it and does not rename it — and its brief. A resume that starts nothing
  reopens the form with what you typed and the reason; if you are in another form or
  notice by then, that is left as it is and a **Not done** notice tells you once you
  have put it away, with what you typed kept on the session's form. Resuming a
  session that is running here, or one that failed with its process still attached,
  ends that process, and the form says so first.
- **Gate** lists the rungs by name, the current one marked *(now)* — *(last run)* on
  an ended card. On a running session, narrowing acts at once. Widening is a row
  ending in *…* and acts after a confirmation that says in one sentence what it
  releases; to a rung that needs containment, only where the session was started
  contained, and never for a session whose directory is inside pptmstr's own
  checkout. Where it was not started contained the row reads *restart…* and opens the
  resume form after the same confirmation, since containment is fixed when the CLI
  process starts. On an ended card each row opens the form under that rung. Every change the driver takes is
  written to `session.json`; a move on a session whose run is over is not, since the
  file's gate is the one it last ran under. HEALTH's *narrow to strict* is the same
  lever as the menu's row; a move made on a running session does not reach the
  agent's briefing, which still describes the rung it started under.
- **Remove from fleet** is offered on an ended card that owes you nothing and has no
  process attached. The card goes and is not recalled at the next start; the session
  is still in New Task's resume picker, and resuming it puts it back.
- **A failed card** has **Close** as well. A session that failed is not over until it
  is closed — its next message would revive it — so Remove is refused until then.
  Close ends the process if one is still attached, and otherwise marks the session
  ended. Closing changes how the file describes
  the end and nothing else: when it failed and what it said are kept, and Details
  shows *closed, after it had failed* with the error.
- A closed card whose session predates `session.json` is titled from the CLI's own
  label for the conversation. **Fork** and **Retry** are greyed on it, because that
  label is not a prompt; Details gives its date as *first seen*. Resuming it does not
  change that: the card keeps the label as its title, the resume form shows it as
  *title* and not as *task*, and Fork and Retry stay refused on every surface that
  offers them.

When a control is refused after you pressed it, a **Not done** notice says why. It
waits for any form that is up to be put away, and never opens over one.

**Ended sub-agents are hidden** once they owe nothing and hold no task: no card, and
no count of them anywhere on the fleet pane. `Session > Show ended sub-agents` is the
one switch that shows them again, for every session at once; unticked, it is
`"hide_ended_subagents"` in `settings.json`, on by default. It changes what the pane
lists and nothing else: every agent is still there, and one its lead wakes is drawn
again.

### A contained agent can ask you to run a command, and pptmstr runs it as you

A contained session cannot install a package, push, or reach a host outside its
allowlist. Its agents can instead **ask**: one exact command line, why, and what they
expect it to change. Asking does not stop the agent — it gets an id back and reads the
outcome later. Nothing runs until you say so at the row, and nothing an agent wrote
decides that for you.

**The row** is in NEEDS YOU, of its own kind. **It is closed until you open it.**
With the cursor on it, it says which command it is and how it stands — *asked 45s ago
· 3rd command asked in this session, 2nd from builder · 0123456789ab*, then *asked:
nothing has run* — and shows none of the command. **A click on the row, or `o` with
the cursor on it, opens it**, and nothing else does: not `j` or `k` arriving there,
not the row above being answered, not the command being asked. Open is of that one
command, by its id, and ends when the cursor leaves the row. No key acts on an open
row: every control takes a mouse click, and run, refuse and cancel each take two.
The first of those answers nothing for 0.6 s after the row opens, and the second
nothing for 0.6 s after the first, so no two clicks of a double-click are two of the
three.

- The command is shown whole, in its own block, escaped: anything outside printable
  ASCII is spelled out (`\u{202E}`, `\x1B`, a trailing space as `\x20`), so a
  character that draws as nothing cannot hide in it. **A command is at most 2,000
  characters and 8 lines, with no blank line in it**, and is bounded as it is drawn
  too: **2,016 characters once escaped** (about 250 characters outside ASCII, or a
  thousand backslashes), **no more than 16 spaces in a row**, and **no more than 12
  commands** joined by `;`, `|`, `&&`, `||` or a line break. Its reason and what it
  expects are **8 lines** each. Anything past a bound is refused where it is asked
  and never reaches you, and your own edit is held to the same. Together they keep
  the block of the largest command there can be to 36 rows in a pane a hundred
  characters wide; the breakdown, the flags and the asker's two texts are under it,
  so the buttons of such a command are still more than a screen down.
- **Above the block, which command this is**: how long ago it was asked, which of its
  session's commands it is and which of its asker's (*3rd command asked in this
  session, 2nd from builder*) and the last twelve characters of its id — things that
  differ between two commands whose text barely does. A command asked in the last few
  seconds says **NEW**, and one whose asker has others waiting says how many.
- **The string is not always the whole of what runs.** Where the command names `git`,
  `make`, `npm`/`yarn`/`pnpm`/`bun`, `pip`/`uv`/`poetry`, an interpreter, a tool that
  does what the directory's files tell it to (`cargo`, `go`, `docker`, `rake`, `just`,
  `gradle`, `mvn`, `eslint`, `mypy`, `gh` and their like), or a path inside the
  session's directory — `./x`, `bin/x`, `$PWD/x`, or the directory written out in
  full — a line says what in that directory it will run: hooks and config, the
  Makefile, package scripts, `setup.py`, the script itself, all of which the session
  can write. `nohup`, `exec`, `env`, `xargs`, `command` and `FOO=1` ahead of a
  program do not hide it. The list is names and a missing line proves nothing:
  anything with a config file or a plugin does the same.
- Under it, the segments as bash would sequence them, with flags for what that reading
  does not see through (substitutions, `eval`, `sh -c`, a heredoc, `&`). **This is
  advisory.** Read the block; the breakdown is a second look and not the gate.
- The shell, the directory, what is taken out of the environment, and the standing
  lines below. The agent's reason and what it expects are behind the author bar.
- **Run** runs the string as shown. **Edit** lets you change it first; what ran is
  recorded beside what was asked, and the asker is told the string was yours.
  **Refuse** takes a reason, which the asker reads as you typed it, and is final.
  While it runs the row stays, with **cancel**.

**A command never opens itself.** An agent can withdraw a command at any time and ask
for another that differs by two letters, and it picks the moment. So the second is
never shown because the first was: it is closed, like every command, until you click
it or press `o` on it. **And when the command you are on leaves the queue** — its
asker withdrew it, it ended, you refused it — no row takes the cursor at all. Where
it was, a line says what left (*the command you were reading — 0123456789ab from
builder — was withdrawn by its asker*), and the cursor is on nothing until you press
`j` or `k` or click a row. That is for the mouse: a command's presses are all clicks,
and the row that slid into its place, opened there, would put its own buttons under
one already on its way down. An approval that leaves is still followed by the row
that took its place, and if that row is a command it is closed.

**What running it means.** pptmstr starts your login shell — `$SHELL`, else the one in
your password entry — as `<shell> -l -c '<the string>'`, in the session's directory.

- **It runs as you, outside the sandbox**, with your credentials and your network, and
  it may execute files the session wrote. That is the purpose, and the reason to read
  the string.
- **Your Claude Code permission rules do not apply to it.** Allow and deny rules and
  hooks in `~/.claude/settings.json`, and managed settings, are enforced by the CLI. A
  process pptmstr starts never passes through them.
- **There is no terminal.** Standard input is `/dev/null`. Anything that prompts —
  `sudo`, an SSH passphrase, a `git` credential helper — waits until the timeout, and
  ends as *timed out*, not as a failure of the command.
- **The environment** is pptmstr's own, minus the virtualenv pptmstr was started from:
  `VIRTUAL_ENV` is unset and that virtualenv's `bin` is taken off `PATH`, so `python`
  and `pip` in the command are yours and not pptmstr's. More is taken out because
  each would let a file in the session's directory run before or instead of the
  command: `BASH_ENV` and `ENV` (a shell sources the file they name), and, from each of
  `PATH`, `LD_LIBRARY_PATH`, `PYTHONPATH`, `NODE_PATH` and `PERL5LIB`, every entry that
  is not an absolute path — an empty one and `.` among them, which is the current
  directory — and every entry that is the session's directory or inside it once links
  are followed, such as `<directory>/node_modules/.bin`. A `PATH` with nothing left,
  or none at all, becomes `/bin:/usr/bin`: a shell reads an empty one as the current
  directory, and bash with none searches a default that ends in `.`. Variables that
  name one thing and that only you set — `LD_PRELOAD`, `ZDOTDIR` — are passed as they
  are. The row lists what is removed whatever the directory is; **what is removed
  because of this session's directory is not on the row yet** — it is applied when the
  command runs and written to the LOG when it ends.
- **One variable is set that you did not set: `PPTMSTR_COMMAND`, to a random value
  made for that one run.** Everything the command starts inherits it, and it is how
  pptmstr tells what a command left running once the shell that started it is gone
  (*The tag*, below). pptmstr shows and records the value nowhere — whoever holds it
  can start a process that is taken for the command's — and where the command's own
  output holds it, the log and the tail have
  `<--------- PPTMSTR_COMMAND value, withheld by pptmstr --------->` in its place.
  The row lists the variable with the changes above. If pptmstr itself was started
  with one — from a command of another pptmstr — it is overwritten, and the row says
  that, without saying what it was.
- **Your shell profile runs first, in the session's directory**, because the shell is
  a login shell. pptmstr cannot see what it does. A profile hook that reads files from
  the current directory — direnv, mise, a dotenv plugin, a version manager's shims —
  reads files the session wrote, and may put a relative entry back on `PATH`. To see
  what yours opens and runs, from a scratch directory seeded with the files such
  hooks look for:

  ```
  mkdir /tmp/profile-probe && cd /tmp/profile-probe
  touch .envrc .env .tool-versions
  strace -f -e trace=openat,execve $SHELL -l -c 'true' 2>&1 | grep -F "$PWD"
  ```

  A line of output is your profile touching the current directory. This has not been
  run against anybody's profile: it is the check to make before you rely on the row.
- **The timeout** is yours to pick at the row: 2 minutes, 10 (the default), 30, or
  2 hours.
- **One command runs per session at a time.** A second is not queued; Run on it says
  why until the first has ended.

**What is killed, and what is not.** The shell leads its own session, and **a command
has ended when no process is left in that session**. The session and not the shell's
process group, because `timeout` and a shell with job control give what they start
process groups of their own, which a signal to the shell's group does not reach. A
timeout or a cancel sends every process group in the session `SIGTERM`; every process
in it then has two seconds to finish what it is doing — the whole two seconds, whether
or not the shell itself has gone — and what is left is sent `SIGKILL`. **"Nothing
left" always means nothing left in that session**: see `setsid` below.

- **A command that exits by itself has its background children ended too**, the same
  way: `make &` does not outlive the command line that started it. Its record says so
  (*N leftover processes of its session … were ended with it*). A command that is
  meant to leave something running has to say so in the string you read — `setsid`, or
  `nohup … &` under `setsid`.
- A process that leaves the session — one that calls `setsid` or daemonises — is
  **never signalled by pptmstr**, and outlives the command's end, the timeout, a
  cancel, and pptmstr. It is looked for, once, when the command ends, by the tag: the
  record then says *N processes it started are ALIVE outside its shell's session, and
  pptmstr did not end them*, with their pids and a `ps` line, and the asker is told the
  same. The pids are as of that moment and there is no kill line beside them: a
  number is handed out again once its process has gone. An ended command with such a
  process owes no row and does not hold its session — leaving a server running this
  way is what `setsid` in the string is for.
- Where `/proc` cannot be read — there is none, or the read failed — the session
  cannot be listed: only the shell's own process group is signalled, and the record
  says *NOT CONFIRMED DEAD* with that as the reason, on every command.
- A cancel pressed after the shell has exited, while its leftovers are being ended, is
  recorded with its time and the outcome stays the exit: nothing is left either way.
- If anything of the session is still there five seconds after `SIGKILL`, the record
  says *NOT CONFIRMED DEAD* and why, beside the outcome.
- **Quitting pptmstr kills every running command**, and its record says *cancelled, by
  shutdown*. `SIGTERM`, `SIGHUP` and `SIGINT` sent to pptmstr ask the window to close,
  so that the same happens (run against a process with no window; not yet against the
  real one). A second signal, of any of the three, kills pptmstr where it stands: that
  is the way out of a shutdown that has hung, and it leaves what was running running.
  A command the quit did not see end within its bound has that written
  in its own directory (`unconfirmed.json`), since the log that also says it is gone
  with the window. **Closing a session does not kill**: the row stays so that cancel
  stays reachable, and the card cannot be removed from the fleet while it does.
- **A pptmstr that dies without quitting — `SIGKILL`, the out-of-memory killer, a
  crash — leaves the command running with no timeout**, out of the terminal's reach.
  Nothing prevents that. What there is: the shell's pid, which is its session's
  number, is on disk the moment the process exists (`started.json`), and at the next
  start pptmstr looks for that session, and for every process that carries the
  command's tag. **Found with its shell still there**, it is a
  row in NEEDS YOU that says **STILL RUNNING, UNSUPERVISED**, with the
  `ps -o pid,pgid,lstart,args -s <session>` line that shows every process in the
  session, with the group each is in, and the `kill -- -<group> …` line that
  signals every process group found in it. **Found with its shell gone**, it is the
  same row for the processes that carry its tag — in the session number the shell led
  or anywhere else — and only for those: the `ps` line names them by pid, and the kill
  line names a process group only where every process in the group carries the tag,
  and single pids otherwise. pptmstr does not kill it for you: it no
  longer holds the process, and a number read from a file is yours to check. No other
  command of that session can be run while it is there; *check again* looks once more,
  and *release session* (two presses) lets go of the session and leaves what runs
  running: nothing is signalled, the record and `released.json` keep that you released
  it and how many processes the last look found, it is not looked for again, and its
  asker is told.
  What a look can answer:
  - *still there* — the row stays, with the lines brought up to date.
  - *gone* — nothing is alive in the session and no process that could be read
    carries the tag; the command reads *end unknown*, and
    `gone.json` is written so that no later start looks for it again. A pid is handed
    out again in time, and a record asked about for ever would in the end be asked
    about some other process.
  - *cannot be said to be this command* — processes are in a session of that number,
    the shell is gone, and none of them that could be read carries the tag. They are
    what the command left — started with an emptied environment, or as another user —
    or another program's that was given the number later. **No row and no
    kill line**: the command reads *end unknown — NOT CONFIRMED DEAD*, with what was
    found, how many could not be read, and the `ps` line to look with, and its session
    is not held. A command started by a build that set no tag is always answered this
    way once its shell is gone.
  - *could not look* — `/proc` could not be read. The row and the hold on the session
    stay exactly as they were, and the line under the row says why. A look that failed
    is never read as *gone*.

  Where there is no `/proc` (anything but Linux) none of this can be looked for, and
  such a command reads *end unknown* at once.
  **Two windows on one session look the same as this**: nothing on disk says which
  pptmstr holds a command, so a second window that restores a session whose command is
  running in the first shows it as unsupervised. The row says so — if another window
  is running the session, the command is that window's, and its timeout and cancel are
  there. Check before you kill. The asker is told the same thing you are: *still
  running, unsupervised*, and not *end unknown*.
- If pptmstr's own runner reports nothing for a command well past its timeout, the
  command is shown as *end unknown*, every process group found in its shell's session
  is sent `SIGKILL`, and a notice says so.

**The tag, and what it cannot see.** A process is taken to be a command's by its tag
when `/proc/<pid>/environ` holds `PPTMSTR_COMMAND` with the value that command's
shell was given — `started.json` holds a SHA-256 of the value, never the value, and
what is read is digested and compared — and the
process started in the same boot as the command's shell and no earlier than it.
pptmstr prints a kill line, and counts a process as a command's, only for what it has
identified that way or by the session of a shell that is still there. What it could
not read it calls unread — never gone, and never somebody else's. It sends no signal
to anything it knows only by the tag.

- **`env -i` and `sudo` strip it.** A process started with an emptied environment —
  `sudo` resets it by default — does not carry the tag and is not found by it. In the
  session of a shell that is still there it is found all the same; past that it is
  one of the processes pptmstr *cannot say is this command*.
- **An environment that cannot be read is unread, not absent.** `environ` is readable
  by the process's own user. A child running as another user (`sudo`, a setuid
  program), and one that has made itself unreadable (`ssh-agent` does), is counted as
  *could not be read* when it is in the session number the shell led. Outside it,
  it is counted only while it is still in the control group the shell was started in
  — `setsid`, `sudo` and going undumpable do not take it out of that — and then as
  *could not be read, may be its*, with a `ps` line and no kill line; such a command
  is never read as *gone* while one is there. That is a tie and not an
  identification: anything else unreadable started from the same terminal in that
  time is counted too. One moved to another control group (`systemd-run`, a new login
  session) is not seen at all, and *gone* says so. So is one whose environment is empty
  (`env -i`): a process also reads as empty for an instant while it execs, so empty
  is read twice and then taken as unread, never as "not this command's".
- **`environ` is what the process was started with.** A process can overwrite that
  block in its own memory, and a program that removes the variable before starting
  children hides them.
- **Linux only.** Elsewhere there is no `/proc/<pid>/environ` and nothing is looked
  for.
- **Whoever holds the value can set it.** The value is random and is in no record,
  so a contained agent that lists another session's command directories — or its own
  — finds nothing to start a process with: the command's id, and the digest in
  `started.json`, are not the tag. What is left:
  - A command that prints its tag in another form (`env | base64`) puts it in its own
    output, which its asker reads. The asker can then start a process that is counted
    as *that* command's — its own — and no other's.
  - A command you approve runs as you, outside the sandbox, and can read another
    running command's environment in `/proc`. That is in the text you approve.
  - A pptmstr that is itself running inside a command has that command's value in its
    own environment, and so do the sessions it starts.
  - A contained agent can make a process of its own unreadable. Started while another
    session's command runs, it is counted beside that command as *could not be read,
    may be its* — never as the command's, so no row, no hold and no kill line come of
    it.
- **A contained session's sandboxed commands cannot read the records at all**:
  `~/.config/pptmstr/sessions` is denied to them whole, the session's own directory
  included, so the output log path `read_commands` names is one a sandboxed `cat`
  cannot open. The CLI's own `Read`, `Grep` and `Glob` are not bounded by the sandbox
  and can still read it.

**Where it is kept.** Each command has a directory,
`~/.config/pptmstr/sessions/<session id>/commands/<command id>/`:

| file | what it holds |
|---|---|
| `request.json` | what was asked, by whom, and when |
| `run.json` | what you approved: the string, the shell, the directory, the timeout |
| `started.json` | the process that came of it: pid (its session's number and its group's), what tells it from a later process with the same pid, a SHA-256 of the tag value its environment was given (never the value), and the control group it was started in |
| `end.json` | how it ended — exit code, timed out, cancelled, refused, withdrawn, never started — and the end of its output |
| `unconfirmed.json` | only when pptmstr quit without seeing the command end: why |
| `gone.json` | only when a later pptmstr looked for the process of a command with no recorded end and saw it gone: when, and what showed it |
| `output.log` | what it printed, from the start, up to 8 MiB |
| `output.tail` | the last 64 KiB, only when the log was cut |

Each file is written once and never rewritten, and **nothing deletes them**: they say
what ran on your machine. The asker is handed the status, the exit code and the tail,
with the log's path for the rest; DETAIL lists a session's ended commands with the
same.

At the next start the records of every session on the fleet are read back, and a
session resumed from anywhere else has its own read when it is resumed. **Nothing
comes back open**: a command that was only asked reads as *lapsed*, and one that was
approved with no recorded end as *end unknown* — or, when its shell's session is found
alive with the shell in it, as *still running, unsupervised*, which is a row with
nothing on it to run —
so no file can put a Run button in front of you. A record that cannot be read is left as it is, logged, and counted in
the status bar. DETAIL marks a command *from before pptmstr restarted*: the files do
not say whether its asker read the result then.

**A briefing can name these tools to a session that no longer has them.** The tools
are offered to contained sessions only, and the briefing that names them is written
when the session starts. A session resumed from contained to uncontained on a CLI too
old to be re-briefed at resume keeps the briefing it had, which names tools its
server no longer offers. Nothing can run through them; tell the session they are gone.

If a record cannot be written, the row and DETAIL say *not recorded on disk* with the
reason. A command whose request was never recorded is not run — Run says why, and
Refuse still works. Under `--fake` nothing is run or recorded: an approval is ended at
once as *never started*.

## Environment

`bootstrap.sh` never installs system packages. If something system-level is missing
it prints the exact command and stops.

```sh
make probe              # stdlib-only environment diagnosis, no venv required
```

The probe is worth running first on an unfamiliar box. `imgui-bundle` ships its own
GLFW, but on Linux that build is **X11-only** and dlopens the X client libraries at
`glfwInit()` — so `pip install` always succeeds and a missing library shows up as
"the window doesn't open" with no traceback. The probe resolves all ten by soname and
prints the install line for your package manager. On a Wayland session it needs
XWayland.

Headless works: `make` routes window targets through `xvfb-run` automatically when
`DISPLAY` is unset.

## Architecture

Read [orchestrator-design.md](orchestrator-design.md) §1 (invariants) and §3
(threading) first. The short version:

```
┌─ Main thread ──────────────┐        ┌─ asyncio thread ─────────────┐
│  hello_imgui.run(...)      │        │  loop.run_forever()          │
│    ├─ drain intent queue   │        │    ├─ ClaudeSDKClient/session │
│    ├─ store.snapshot()     │        │    ├─ PreToolUse gate        │
│    ├─ build UI from snap   │        │    └─ emits intents          │
│    └─ render + idle        │        │                              │
└────────────────────────────┘        └──────────────────────────────┘
              └──────────── Bridge (two crossings) ────────────┘
```

- The **store** is the single source of truth. The UI owns no application state and
  builds every frame from exactly one snapshot, taken atomically at frame start.
- Records are immutable, so snapshotting is a reference swap rather than a deep
  copy — the difference between a Python UI that can rebuild every frame and one
  that cannot.
- **The main thread is the store's only writer.** The asyncio thread never touches
  it; it enqueues intents and the UI applies them between frames. That is why the
  store needs no lock.
- Transcripts are the one deliberate exception: append-only and internally
  synchronised, written directly by the asyncio thread, because routing a token
  stream through the intent queue would put one queue item per token on the UI
  thread's critical path.

### Layout

| Path | |
|---|---|
| [pptmstr/model.py](pptmstr/model.py) | Immutable records: `AgentRecord`, `Snapshot`, `ContextSnapshot` |
| [pptmstr/store.py](pptmstr/store.py) | Copy-on-write store; the single audit point for every mutation |
| [pptmstr/intents.py](pptmstr/intents.py) | Every way the world can change, as values |
| [pptmstr/bridge.py](pptmstr/bridge.py) | The only place threads and asyncio meet |
| [pptmstr/transcript.py](pptmstr/transcript.py) | Append-only per-agent output buffer |
| [pptmstr/pool.py](pptmstr/pool.py) | Bounded concurrency; over-cap sessions queue |
| [pptmstr/approval.py](pptmstr/approval.py) | Classification, summaries and diffs for the gate |
| [pptmstr/ui/](pptmstr/ui/) | Fleet rail, inbox, detail, health, transcript pane, launcher, shared widgets |
| [pptmstr/theme.py](pptmstr/theme.py) | Semantic colour roles; light, dark, high-contrast, and six discretionary |
| [pptmstr/driver.py](pptmstr/driver.py) | One `ClaudeSDKClient` per session, translated into intents |
| [pptmstr/app.py](pptmstr/app.py) | Runner, docking layout, and the frame loop |
| [scripts/probe.py](scripts/probe.py) | Environment diagnosis; never installs anything |
| [scripts/screenshot.py](scripts/screenshot.py) | Renders the UI headlessly to a PNG |

## Development

```sh
make check      # lint + typecheck + test — what a CI gate would run
make format     # black, in place
make test
make bench      # idling CPU and cross-thread wake latency
make shot       # render the UI to a PNG
```

`make bench` on a Debian 12 / XWayland / Radeon box, at the default `fps_idle` of
9.0 (`settings.py`; settable in the settings file or with `--fps-idle`): a session
parked and waiting on you costs **2.0% CPU against 13.3% at full speed**, and an
agent going active takes the app back to 60fps — which is what proves `any_active`
drives idling rather than the app simply idling all the time. Cross-thread wake
latency is 69ms.

The **cold-start screen costs more**, and it is the one number here with a
condition attached. With no sessions at all, the NEEDS YOU pane fills with an
animated splash, and resting on it costs 4.7% rather than 2.1%. That is the whole
of the difference — seed a single parked session and TRIAGE measures 2.1% against
FOCUS's 2.0%, so the animation costs nothing once you have started anything. It is
a cost you pay only on an empty application, and only until you press Ctrl+N.
Numbers from `scripts/bench_idle.py`, twelve runs per layout; see
[orchestrator-design.md](orchestrator-design.md) §4.3 for the full table and the
conditions.

Formatting is `black`; `ruff` is lint-only here, so the two can never disagree about
the same file. `mypy` runs `strict` against the 3.11 floor rather than the local
interpreter — typechecking against the oldest supported version is what catches a
3.12-only idiom before it reaches a Debian 12 box.

Design decisions are recorded in [orchestrator-design.md](orchestrator-design.md)
with their reasoning, including the ones that were later found to be wrong. If you
think one is wrong, engage with the recorded reasoning rather than starting over.
