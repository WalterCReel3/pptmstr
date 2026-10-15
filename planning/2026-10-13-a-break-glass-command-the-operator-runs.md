# A break-glass command: the contained team asks, the operator reads it, pptmstr runs it

**Dated:** 2026-10-13 · **Status:** a design agreed in conversation with the operator; built since —
[`2026-10-13-building-the-break-glass-command.md`](2026-10-13-building-the-break-glass-command.md)
records what and how far it was verified · **Found by:** the operator, asking
whether work had happened on "the autonomous workteam that has access to a
break-glass/privileged-actor concept" · **Continues:**
[`2026-10-07-a-session-can-continue-under-a-different-mode.md`](2026-10-07-a-session-can-continue-under-a-different-mode.md)
(its D3, and the three mechanisms it weighs)

**(D)** the operator's words — the decision is what the words say and no more. **(R)** the
lead's recommendation, not agreed. **(O)** open. Nothing here was measured; where a statement
rests on how the CLI, the SDK or pptmstr works, it is by reading or from general knowledge and
says so.

## What was said

**D1.** "It sounds like the break-glass actor still needs some thought and design. Is this best
solved with inter-team coordination within the same repo/worktree?"

**D2.** After the lead proposed that the unsandboxed actor be the pptmstr process itself, with
the operator approving one exact command: "I like the idea of the pptmstr process, with the
user, being the actor."

**D3.** "Let's drop the inter-team angle and refine based on the break-glass request idea."

**D4.** To four questions: whether the asking agent waits — "Carry on. We don't want to end up
with another mechanism to turn the session synchronous."; commands granted in advance for
unattended runs — "Leave for second."; whether the operator may change the command before it
runs — "Editable, yes."; which environment it runs in — "Login shell."

**D5.** (2026-10-15, after the feature was built and merged) "If you could also make the
break-glass interaction less verbose, that would be helpful. I feel it should mirror any other
bash request but with a few more warnings (but without them being full paragraphs)."

## Why not a second team, and why not a model at all

Recorded because D3 drops the angle and the reasons should not have to be found again.

- A sub-agent cannot be the actor: the sandbox belongs to the CLI process and sub-agents share
  their lead's (the 10-07 record, measured).
- A privileged *model* session that reads what contained agents wrote is a prompt-injection
  path into a process with the operator's whole machine.
- Whoever runs `make install` outside the sandbox executes files the contained team wrote. No
  design removes that; the gate that exists is a person reading the exact command and knowing
  it runs repository-controlled code.
- An operator who wants an elevated team can have one today by hand — a contained team and an
  attended, uncontained session in one directory, coordinating through files. pptmstr connects
  nothing across sessions; `touches` does not order two boards; the injection exposure stands
  and the strict gate on the elevated session is what bounds it.

## The shape (D2–D4, with the lead's detail marked)

**The request.** A bus tool, offered to contained sessions: one exact command string, the
reason, what it is expected to change. **(D4)** It returns an id at once; the agent carries on
and reads the outcome later, as with a decision. **(R)** A cap on open requests per asker; the
result goes to the asker and its session's root may read it; text bounded where it is accepted.

**What the operator sees.** **(R)** A needs-you row of its own kind, never drawn as an approval
or a decision: the command verbatim and whole in its own block, not flattened, not ellipsised,
control characters and bidirectional or look-alike Unicode made visible; under it the programs
it invokes, from pptmstr's own shell splitter (`approval._split_segments`, the one checked
against `/bin/bash` by `make verify`); the directory and the environment; one standing line —
this runs as you, outside the sandbox, with your credentials and network, and may execute files
this session wrote; the agent's reason behind the author bar. Actions: run (two presses, no
shortcut key, under the same keystroke hold as the answer box); refuse, with a reason;
**(D4)** edit, then run — recorded as edited, and the asker is told what actually ran.

**Superseded in part, 2026-10-15, by D5.** "Never drawn as an approval" and the standing
paragraphs are withdrawn: the row now reads as a Bash approval does — the queue's summary line is
`approval.summarize` of the command, as a Bash row's is, behind "outside the sandbox · "; the
opened row is one-line warnings, a bordered body naming `command`, `reason` and `expects` as a
Bash body names `command` and `description`, and the Bash row's button line (reason box,
run, refuse, edit). What stays, because they are properties and not wording: the command is
drawn whole through `commandtext.visible` in its numbered block, never cut by Dear ImGui; the
string run is the frozen draft the operator saw; no key runs, edits or refuses; the row is
closed until the operator opens it; edit-then-run is recorded as edited. Run keeps a
confirmation in the Bash button's place — "Run outside sandbox" turns into "Press again to
run" for a few seconds — while refuse is one press, as a Bash reject is, with what the
row's reason box holds; the segment breakdown is one line of names, and the full sentences
(every flag, the advisory, the profile, the environment) are behind the row's `(?)`. DETAIL
draws the same warnings and body with no button, as it draws a Bash approval. Built in
`ui/command_view.py` and `ui/inbox.py`; the lead's decision on the confirmation and the
builder's on one-press refuse, which the user may overturn.

**2026-10-15, the lead's decision:** Run is not held by a pane that failed to draw elsewhere
(DETAIL), as an approval is, and that stands — its press is reachable only from the row that
drew the frozen, bounded string in that same frame, so the operator has read what the button
runs wherever else failed.

**What pptmstr does.** **(D4)** The operator's login shell runs it (`$SHELL -lc`), in the
session's directory. **(R)** Off the draw thread; stdin closed; its own process group; a
timeout that kills the group, and a cancel that does the same; one running command per session.
Output goes to a file under `~/.config/pptmstr/sessions/<id>/commands/<n>/` beside a record:
who asked, the string as asked, the string as run, whether it was edited, when, the exit status
or the timeout, how long. These persist — they changed the operator's machine. The asker reads
back the status, the exit code and the tail of the log, with the log's path for the rest.

**Not in the first version.** **(D4)** Commands granted in advance; **(R)** interactive
commands and `sudo`; output streamed while it runs; more than one at a time. The record's
layout leaves room for grants.

**For the agents.** The unattended briefing tells a team today to "record as blocked, naming
what it needed"; it would say to ask with this tool, and that asking does not stop the work.

## What is different from the CLI's own Bash, to be designed for

By reasoning about the pieces, not by a run.

- **The operator's Claude Code permission rules do not apply.** Allow and deny rules and hooks
  in `~/.claude/settings.json` or managed settings are enforced by the CLI; a process pptmstr
  starts never passes through them. The row should say so.
- **Full credentials and network.** The sandbox denies `~/.ssh`, `~/.aws` and the Claude
  credential files and allows one host. This has all of it. That is the purpose, and the
  standing line says it.
- **The environment.** A login shell (D4) is the answer to pptmstr's own virtualenv being on
  `PATH`; what the shell's profile does, and how long it takes, is the operator's.
- **No terminal.** Anything that prompts hangs until the timeout, which must be reported as a
  timeout and not as a failure of the command.

## Open

- **(O)** Whether the tool is offered only to contained sessions, or to any — an uncontained
  one can already ask for `Bash` through the ordinary gate.
- **(O)** The default timeout, and whether the operator sets it at the row.
- **(O)** Whether the asker's lead is told in the digest's waiting slot, as a decision is.
- **(O)** Grants, for the second version: exact strings typed at launch that run without a row.
  Each runs repository-controlled code with nobody reading it first.
- **(O)** The other size of the problem, unbuilt and not agreed: "continue as strict, outside
  the sandbox" for a live autonomous session — the whole-session break-glass the 10-07 record
  recommended. Today's gate menu offers such a session only "narrow to strict", which keeps the
  sandbox on.
