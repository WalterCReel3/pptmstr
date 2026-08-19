# An expanded group outgrows its pane, and the filter rule changes at the cap

**Dated:** 2026-08-13 · **Status:** open, not started ·
**Extends:** [`2026-08-13-a-card-is-an-agent.md`](archive/2026-08-13-a-card-is-an-agent.md)

The card design in that note sizes an expanded session from
`len(projects.subagents_of(...))` and shows every sub-agent, terminal ones included.
That is deliberate and the reasoning holds: the list is append-only
(`projects.py:103-109` applies no state filter, nothing reaps terminal subs), so
expanded height is *monotone* — it never shrinks, and order never changes because
`snap.order` is append-only.

Two costs of that choice were not priced. Both are sharper than they first looked,
because the first implementation read a sub-agent as a one-line row and the design
does not: a card stands for an agent, so a sub-agent is a **full card inside a session
group**, not a row inside its parent's card. That was the note's reframe all along —
"a session is then not a card but a *group*" — and the slot table it gives (role,
state, spend, model, topic) is a card's worth of content, which is why model had
nowhere to go when it was tried as a row.

**A full card is several times a row.** The vertical arithmetic below is written for
cards; an earlier draft of this note assumed rows and understated it by roughly the
ratio of `_LINES` to one.

## The expanded group has no upper bound, and the pane does

Group height is the sum of its member cards, each sized by its own `_LINES` class.
There is no cap. A session with five sub-agents can exceed the FLEET pane where
fifteen rows would not have.

This was invisible while a session was one card of one to three lines. It became
reachable the moment groups landed, and it was aggravated by a defect the same change
exposed: the rail pinned the view with `set_scroll_here_y` on every frame a focused
card drew, because `RailState.scroll_to_focus` was read and never assigned.
`widgets.py:38-47` records that failure mode for `follow_tail` already — "the view
snaps back before any position test could notice the operator trying to leave it."
Composed, a focused oversized group could not be scrolled and its lower cards could be
neither read nor clicked.

**That scroll defect is fixed** — the flag is now a one-shot armed on cursor change.
It is load-bearing for this feature rather than a nicety, and it should not be
regressed into an unconditional pin again.

## The cards the operator opened the group for are the ones at the bottom

Append-only order is spawn order. Terminal sub-agents accumulate at the head and live
ones sit at the tail, so a long-running session's group is a stack of DONE cards with
the answer to "what is running right now" furthest from the disclosure that was just
clicked — and, at card heights, most likely off-screen.

## Two obvious repairs, both wrong for recorded reasons

**Filter to non-terminal sub-agents.** This is what §5 step 2 of
[`2026-08-13-sub-agents-are-invisible-while-they-work.md`](2026-08-13-sub-agents-are-invisible-while-they-work.md)
recommends, and the superseding note refutes it specifically for the variable-height
case: the group then oscillates at sub-agent lifetimes and the stability problem
returns inside the group. That refutation stands.

**Order live-first.** Cheaper-looking and worse. It preserves height but destroys the
other half of the stability property — that a card stays where it was — and does so at
the model's pace, which is the motion the collapse rule exists to make opt-in.

## The prohibition is conditional on a variable height, and a cap removes the condition

The note's rule is not "never filter". It is:

> Filtering inside a fixed-height pip row is free; filtering inside a variable-height
> card costs stability. **The two rules must differ.**

A capped group is bounded, and past the cap it is *fixed*. The condition attached to
the prohibition lapses at exactly the size where filtering starts to matter.

**Proposed shape**, to be argued before it is built:

- Group height is the sum of at most `CAP` member cards plus a summary line. It steps
  at most `CAP` times over a session's life and is constant thereafter — strictly more
  stable than the uncapped version, not less.
- Within the visible cards, prefer non-terminal sub-agents. Below the cap the group is
  variable-height and the prohibition applies, so render append-only there. At and
  above the cap the region is fixed-height and the pip-row rule applies instead.
- One summary line carries what is not shown (`+7 earlier · 5 done`), so the omission
  is stated rather than silent.

The seam between the two regimes is what needs argument, not either side of it. A
group that switches ordering rules as it crosses `CAP` may read as a glitch at the
boundary even though each regime is individually correct; the alternative — applying
the fixed-height rule from the first card and accepting oscillation on small groups —
trades a rare visible seam for a common invisible one.

`CAP` should be chosen against the pane, not picked round. At card heights the honest
number is small, likely three or four, which makes the summary line the common case
rather than the exception — and that in turn raises whether the summary line should be
clickable into a fuller view. DETAIL is the natural home for "all sub-agents of this
session"; see [`2026-08-13-detail-swaps-to-a-deliverable.md`](2026-08-13-detail-swaps-to-a-deliverable.md).

## A consequence of cards that rows did not have

Height now varies with sub-agent *state* as well as count, because density classes
apply per card and a sub-agent parking for approval promotes its own card from
`active` to `blocked`. That is acceptable and does not weaken the collapse rule: the
parent note already concedes that `_LINES` class changes move cards constantly and
that what actually holds is *ordering*. Expansion is opt-in, so an operator who opened
a group asked for the motion inside it. Collapsed groups remain immune, which is the
invariant that mattered.

## Not in scope here

Per-sub-agent context rings remain unbuildable from the SDK; see the parent note.
Nothing here changes that, and a cap does not make the slot more obtainable.
