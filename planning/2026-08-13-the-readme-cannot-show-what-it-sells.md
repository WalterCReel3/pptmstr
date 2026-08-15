# The README sells density and cannot show any

**Dated:** 2026-08-13 · **Status:** open, not started ·
**Follows:** the README rewrite that put three image placeholders in the document

The README now claims that everything the fleet is doing is on screen while it is
doing it. It demonstrates this with prose. Three `<!-- HERO -->`-style comments mark
where captures go; none of them can be filled today, and the reason is not that
nobody has run the screenshot script.

## What actually blocks it

**Captures are not reproducible.** Checked, not assumed — two consecutive runs of the
same command:

```sh
make shot && md5sum shot.png    # 9593e650b9b026d93c0d44b83e5de679
make shot && md5sum shot.png    # 01a3e6f6556b5a6da46d13fd49bfa6f4
```

Not pixel noise. The two frames differ in content: five pending calls versus six, ten
agents versus eleven, different cards in the rail. `FakeDriver` takes a fixed seed
(`fake_driver.py:124`) but `_tick` mutates state on a 0.6s wall clock
(`fake_driver.py:135`, `:322`), so what lands in the capture depends on how long 120
frames happened to take on that box. Persisted `imgui.ini` and
`remember_selected_alternative_layout` (`app.py:727`) can independently move the
docking arrangement out from under a shot.

This is the same objection that put `shot.png` in `.gitignore` in `88ae634` —
"a capture of one run, not a reference image" — and it binds harder for a committed
README asset than it did for a scratch file, because a README asset is one somebody
will eventually need to regenerate and match.

**Cost is never seeded.** `fake_driver.py` sets no cost on any record; there is no
`cost`, `usd` or `price` in the file. Every card in every fake capture reads `$0.00`.
A hero image selling "cost is on screen" that shows zeros everywhere argues against
the sentence it sits under.

**The fixture is composed for defects, not for a first impression.** It seeds a FAILED
agent with "tool call rejected" and an over-long Bash pipeline that deliberately
overflows a row (`fake_driver.py:63-91`, `:186`). Those are correct choices for a
capture used to check rendering. In the current TRIAGE capture they put a red failure
box at top centre and a red card in the rail, and roughly half the frame is empty —
the inbox below the fold and the DETAIL pane below its header. As the first image in
the document it sells fragility and empty space.

## What it needs to become

The operating requirement, from the decision to treat re-shooting as routine
maintenance: **asking for new hero images must be one command, not a craft session.**
The UI will keep moving and these images will go stale on a regular cadence. Anything
that requires a human to nudge a fixture until the frame looks right will not survive
the second refresh, and the README will carry images of a version that no longer
exists — which is worse than the placeholders it has now.

That rules out `scripts/mock_cards.py` as the source, despite its `--view` presets
being exactly the right shape. Its own docstring marks it for deletion once the layout
landed, and it talks to no Store, Bridge or SDK. Pinning marketing material to a mock
of a layout is how the mock stops being deletable.

Rough shape, in the order it unblocks something:

| | |
|---|---|
| determinism | tick on frame count rather than wall clock, or a `--freeze` that seeds and stops advancing |
| layout pinning | capture runs must ignore the persisted ini, not race it |
| a capture fixture | seeded cost, a frame composed to fill, failures present but not dominant |
| one target | `make shots` writes every `docs/images/*.png` the README references |

## Open, and worth settling before building

- **Whether the capture fixture is `FakeDriver` or a sibling.** Sharing it keeps one
  fixture honest against the real records. But the two have opposed goals — the dev
  fixture wants edge cases visible, the capture fixture wants a frame that reads well
  — and a single fixture serving both is how it ends up serving neither. A `--freeze`
  plus a distinct seeded scenario is probably the smaller commitment.
- **Whether `docs/images/` is exempt from `88ae634`.** The recorded objection is to
  checking in an unreproducible capture. If determinism lands first, the objection is
  answered on its own terms and the images are ordinary committed assets. If it does
  not, committing them anyway spends a decision that was made for a good reason, and
  that should be an explicit amendment rather than a quiet one.
- **What the fourth capture is, if there is one.** The transcript pane's styling by
  kind is a real differentiator and is currently sold in a sentence with no picture.
  It may not survive as a separate image if the hero already shows the pane.
- **Whether a stale image is worse than none.** It decides how hard the refresh needs
  to be wired — a CI check that the images postdate the last `pptmstr/ui/` change is
  cheap and would answer it mechanically.

## Why not now

The prose problem and the image problem are independent, and the prose problem was the
one costing something on every read: the document led with a disclaimer about the
name rather than a claim about the tool. That is fixed and did not need a single
capture. What is left is a build task in `fake_driver.py` and `scripts/screenshot.py`
with a real design question inside it, and it should not be smuggled in as part of a
documentation edit.
