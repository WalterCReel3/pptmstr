# A fleet that survives the window, a detached session, and controls on the card

**Dated:** 2026-10-13 · **Status:** decided (D1–D8) and built on `enhanced-sessions`, uncommitted, seen drawn
by nobody; the two known issues below stand · **Found by:** the operator, while dogfooding `enhanced-sessions`

**(D)** the operator's words, quoted — the decision is what the words say and no more.
**(R)** the lead's recommendation, not agreed. **(O)** open. "Read" means established by reading
the tree on the day; nothing here was run.

## What was said

**D1.** "As I've been using this think a persisted 'fleet' would be the next step so that a
relaunched application would immediately beneficial to a regular user. But since the fleet can
get quite full we may need a new concept for a session: Detached. That is where it is first,
inactive (closed), _and_ removed from the UI. So when the application relaunches the fleet is
pre-populated with the sessions there were last active and not detached. This may also require a
need to 'hide' inactive subagents as well (a settings config)."

**D2.** "I think session controls deserve a right click context menu on the card in the fleet
pane. Brainstorming interactions: Right Click (Active) -> (Close, Fork, Interrupt, Details,
Health), Right Click (Inactive) -> (Resume, Fork, Remove, Details). 'Details' is new and would be
a simple modal that describes the session's parameters, cwd, gating, etc. … The action list may
not be exhaustive or correct in all cases but is an initial concept."

**D3.** On two windows: "that would be a rare case of usage. A predictable behavior should be
fine. … a launched window simply tries to launch with the same set of sessions that are not
removed/detached." And then: "Let's punt on the two window guard but note it as a required fix
in the future (beta level known issue)."

**D4.** "the context for the session card may also carry the controls for narrowing and widening
permissions (or set autonomous) if it makes sense." · "Agreed on the keyboard controls." ·
"Details may also allow copyable displays of paths used for all different aspects of the session
(CWD, transcript path, board path, etc)."

**D5.** "Should it be a global fleet directory where files are sessions with locks and owners?"

**D6.** Answers to three questions the lead put:
resume from a closed card — "Through the launcher pre-filled. May need a different UI to
streamline the launch and make certain parameters immutable to remove confusion of a resume.";
reading a closed session's board without resuming it — "Not now. Future updates for session
key'd tasks (sounds like it's worth doing though)."; hiding ended sub-agents that owe nothing and
hold nothing — "On by default, yes."

## The shape so far

**Three states, and pptmstr deletes nothing.** *Live*: a process, as today. *Closed*: no
process, the card still in the fleet — what a relaunch pre-populates (D1). *Detached*: closed
and off the fleet; still on disk, still in the resume picker, re-attachable from there. The menu
word is "Remove from fleet" (D2's "Remove"), and it is offered only on a closed card, so
detached always means it was already closed.

**Pre-populating is not relaunching (R).** Cards restored at startup are inert: no process, no
spend, no sandbox question. A closed card shows no board until it is resumed (D6); the board log
gives it back then.

**The card's menu (D2, D4), with the lead's amendments (R):**

| Card | Items |
|---|---|
| Live | Interrupt (greyed unless mid-turn) · Fork · Gate ▸ · Details · Health · — · Close (confirms when mid-turn) |
| Closed | Resume… · Fork · Details · — · Remove from fleet; and on a failed card, Retry in a new session · Dismiss, which today exist only on the needs-you row |
| Sub-agent | Details |

The same menu opens from a key on the focused card (D4).

**Gate ▸ (D4), and what the driver already permits (read).** Narrowing a live session to strict
is always possible (`AgentSession.revoke_policy`). A live widen is refused on a session that was
launched without containment when the target rung requires it, and nothing in the UI calls the
widening path today. So: narrowing acts at once; widening acts at once where the guard admits
it; "autonomous" on a session with no containment is *Continue as autonomous…* — close and
resume through the resume form, which is the mechanism the 2026-10-07 record held back until a
resumed session was measured to be sandboxed. It was, on 2026-10-12 (n=1). **(O)** A live change
of gate leaves the agent's briefing describing the rung it started under; a resume now delivers
the new briefing.

**Details (D2, D4).** A modal of the session's parameters, each path copyable: session id;
directory and its git layout; team template; model; the gate *as granted* and whether it is
contained; started, last active, cost; the transcript, the brief directory, the board log and
whether the board is being recorded. **(R)** It is also where the launcher's boundary paragraphs
go — the launcher keeps one line per fact (the 2026-10-13 dogfooding record's note) and Details
carries the argument. And what Details shows for a closed session is exactly what the persisted
fleet has to store: designing the modal designs the file.

**The resume form (D6).** Not the New Task modal with fields filled in: its own, shorter form.
**(R)** Immutable and shown as facts: the session, its directory, its team. Choosable: gate,
model, the sub-agent cap. **(O)** Whether a message is required. Today a resume sends the task
as the session's next message; a fleet of closed cards wants "open it and wait for me" as well.
Whether the driver can start a resumed session with nothing to send is not read yet.

**Hiding sub-agents (D1, D6).** On by default: an ended sub-agent that owes nothing and holds no
claim is folded into a count on its lead's card, which opens. **It is a filter on what is drawn,
never a removal from the store** (read: `intents.AgentRemoved` exists and nothing emits it;
`Snapshot.left_behind` and the reclaim rule rest on that — removing a record changes who counts
as gone).

**Where it is kept (D5).** **(R)** Yes: one file per session, not one index file. Per-entry
atomic writes mean one window's change cannot undo another's, and a lock and an owner have an
obvious place. Recommended layout, one directory per session holding everything pptmstr knows
about it:

    ~/.config/pptmstr/sessions/<session id>/
        session.json     launch parameters, closed/detached, last active
        board/           the board log (today: ~/.config/pptmstr/boards/<session id>/)
        lock             pid, start time — when the two-window guard is built

The rule in `pptmstr/sessions.py` stands: the CLI's transcripts decide what exists; a session
file is decoration for a session that does, never evidence of one. A file whose transcript is
gone draws as such and can only be removed. **(O)** Moving the board log under it changes a path
that shipped yesterday and holds one operator's logs; it is cheap now and not later. **(O)**
Whether the existing `sessions.json` (bookmark, title) folds into `session.json`.

## Known issues, by decision

- **Two windows on one fleet (D3): a required fix before beta.** Every window opens with the
  same closed fleet, and two can resume one session. Nothing guards it across windows: within a
  window a launch over a live id supersedes the old run; across windows the board log notices
  two writers only at the next restore, marks the record contested, and stops recording that
  session. Whether the CLI locks a transcript is unmeasured. The fix is the `lock` file above:
  taken on resume, a dead owner ignored, the other window's card reading "open in another
  window" with Resume greyed.
- **Task ids are fleet-wide (D6): deferred, wanted.** Two sessions that both use `t1` collide.
  It is why a closed card shows no board. Keying tasks by (session, id) is the fix and is not
  small.

## Decided after the first write-up

**D7.** To three points the lead put — moving the board log under the per-session directory:
"Move it."; whether a message is required on resume: "Let's try a message for now but prefilled
with the last invocation message?"; the build order below: "Looks good."

So: the per-session directory is the layout, and `boards/<session id>/` moves to
`sessions/<session id>/board/`; the resume form has a message box, prefilled with the last
message the operator sent that session, and a message is required for now.

**Build order (D7):** 1. the card's context menu and Details — useful at once, and Details fixes
what the file must hold; 2. the per-session file, the board log's move, and closed cards at
startup; 3. Remove from fleet and the hiding of ended sub-agents; 4. the resume form and the
gate menu.

**D8.** On the five places the plan departs from this record (the resume form's gate defaulting
to strict; a session that had failed coming back as an ordinary closed card; sessions older than
this build having no known team and an empty message box; a session removed in this window
keeping its task ids until exit; an open group shrinking as its sub-agents end): "Those previous
concerns are fine. Especially closing the failed card. That was an annoyance bug."

## Not decided

**(O)** Whether the existing `sessions.json` (bookmark, title) folds into `session.json`.

## Corrected after the operator used the build

**D9.** Three things were built differently from what was said, and the operator called each
out on 2026-10-13. The plan relayed to the builders carried the lead's and the planner's
elaborations as though they were the request, and nobody building could see anything drawn.

- **Hiding sub-agents.** Said (D1): "a need to 'hide' inactive subagents as well (a settings
  config)". Built: a count on the lead's card that opens a fold. The operator: "ignored an even
  more straightforward request to completely hide them, full stop, with a global toggle setting
  to display them." So: with the setting on, a hidden sub-agent draws nothing and nothing counts
  it; the setting is the only way to see them. Which sub-agents are exempt (D6) stands.
  Two things the builder settled, the session being unattended. **The collapsed marker counts
  only what would be drawn** (`N subs` was every sub-agent, which is a count of the hidden
  ones), so a session whose sub-agents are all hidden has no marker and nothing to open; the
  operator's open/closed choice is kept, and the group is open again when one is woken. This
  departs from "the collapsed marker still counts every sub-agent" in the plan. **The cursor
  keeps nothing drawn.** It can be on a sub-agent that then becomes hidden (the operator
  clicked its card and it ended, or answered the last thing it owed); the cursor is not moved
  and the lead's card lights for it, as it does for every member of a collapsed group. The
  other panes still show that sub-agent, which is where the operator left the cursor.
- **The resume form.** Said (D6): resume from a closed card. Built: the launcher also hands a
  resume picked in its own list to the short form. The operator: "That is incorrect. That
  should have only been for an inactive card." So: the short form opens from a closed card
  and nowhere else; a resume picked in the launcher is finished in the launcher.
- **Air.** Said (the dogfooding record's D2): "Some margin or 'air' for the dialogs." Built:
  edge padding on the resume form alone. The operator: "a lot of margin _around the edges_ but
  the UI elements were still crowded ... and it wasn't applied to _both_ launchers". So: one
  dialog style, used by both, that spaces the controls from each other.

**D10 (2026-10-14).** On the dialogs and the card's menu, after looking at them:

- "Why are the stylings of the launchers so incredibly different? ... there's suddenly a dark
  gray in the 'Resume session' dialog. I think that color has a potential function to
  distinguish text entry fields from the rest of the dialog but its application feels
  incomplete." Cause (read): the resume form's body is a child window and shows the docks'
  panel colour; popups and input frames share the other. So: one dialog surface, entry fields
  as wells in a second colour, both from `widgets.dialog_style()`.
- "there are so many overwhelmingly verbose disclaimers all over the launchers. If we have
  notices then we may simply have a question mark that expands a modal explanation. But even
  putting that verbosity in the right-click context of the fleet card is an, interesting
  decision." So: no paragraph of explanation in a launcher unless a `?` was pressed for it; at
  most one short line under a control.
- Whether the autonomous warning stays visible: "_That_ one should be a bit loud, yes."
- Whether the card's menu gets a `?`: "No `?` at all. That is more of user-guide / help
  document issue in the future." So the menu is labels, a few words on a greyed item saying
  why, and the confirmations that already exist; its explanations are not drawn anywhere yet.

Screenshots work in a contained session through `scripts/with_display.sh` (an Xvfb over TCP in
a user namespace, the recipe `../imgui-adv-text` documents). The lead's "no display" finding
had tried only the unix-socket form; every "not seen drawn" in these records dates from before
this.
