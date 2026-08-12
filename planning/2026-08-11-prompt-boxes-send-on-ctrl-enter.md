# Every prompt box sends on Ctrl+Enter

**Dated:** 2026-08-11 · **Status:** built · **Supersedes:** the task-box binding in
`2026-08-10-launcher-as-a-modal.md` §"What the modal exposes" (`Enter` launches,
`Ctrl+Enter` breaks a line)

`Ctrl+Enter` submits and `Enter` inserts a newline, in the launcher's task box and
in both reply composers. One binding, one constant, `widgets.CTRL_ENTER_SUBMITS`.

## Why the recorded decision was overturned

The launcher's binding was chosen so the common case would not need the mouse, and
that reasoning stands — it was an argument against a mouse-only submit, not an
argument for `Enter` specifically. `Ctrl+Enter` satisfies it identically.

What changed is that the task box stopped being the only prompt box. Answering a
running agent is the interaction this application exists for, and a reply is
routinely several lines: a plan to react to, a correction with a path in it, a
pasted error. Under `Enter`-submits those are all sent one line at a time. The
argument that a prompt is usually one paragraph was true of the launcher and false
of everything downstream of it.

Leaving the launcher alone would have been worse than either binding applied
consistently. The two boxes look identical and sit one keystroke apart in the same
workflow, and the direction of the mismatch is the expensive one: muscle memory
from a reply box trains `Enter` as *newline*, and the box where that misfires
spawns a session on half a sentence. A mis-sent reply costs a turn; a mis-launched
session costs a subprocess, a pool slot and the cleanup.

## How it is bound

One flag: `enter_returns_true`, and deliberately **not** `ctrl_enter_for_new_line`.
ImGui's multiline enter branch validates on
`(!ctrl_enter_for_new_line && io.KeyCtrl)`, so `Ctrl+Enter` already *is* the
validation chord and `ctrl_enter_for_new_line` is what inverts it into
`Enter`-sends. The binding is therefore a flag that must stay absent, which is what
`tests/test_widgets.py::test_submit_binding_is_enter_returns_true_without_the_inverter`
exists to hold down — adding it back would swap the two keys and break nothing else
visibly.

The mask changes what the first element of `multiline_input`'s result means: true
on submit, never on a keystroke. Both composers now store the returned draft
unconditionally rather than gating on it; gating would have persisted nothing until
send and lost the draft on every pane switch.

`ComposeState.focus_reply` is now set after a send. ImGui clears the active id on
validate, so without it the caret leaves the box on every reply — which it did
before only because nothing ever submitted by key.

The tool-argument editor keeps the default binding. It holds JSON, not a prompt,
and `Enter` there is a newline under either scheme.

## Verified

`tests/test_compose.py` covers the send rule and the whitespace guard — which
matters more under this binding, since a box that takes `Enter` as a newline is
what accumulates a draft of nothing but newlines. Flag arithmetic is pinned in
`tests/test_widgets.py`. Full suite green; `mypy`, `ruff` and `black` clean.

Not verified by injected key events against a live window, unlike the modal work in
the superseded doc. The ImGui branch was read rather than exercised.
