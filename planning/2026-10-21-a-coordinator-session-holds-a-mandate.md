# A coordinator session holds a mandate, not a superuser's reach

**Dated:** 2026-10-21 · **Status:** concept. Nothing here is decided or on a board, and no
research has been run for it. Everything below "The operator's words" is the lead's analysis from
2026-10-20, read against the inter-lead channel's records and not measured. "Coordinator" is a
working name only (operator). · **Raised by:** the operator, as feedback and then as a spitball,
during round three of `feat/inter-team-linking`.

## The operator's words

> A higher level manager prompt that can interact with and direct the task team leads would be
> useful too

> basically my "I don't remember which tab was doing this thing, you figure it out"

> how would an OPTIONAL "coordinator session", whose sole purpose is more automation on top of
> session leads, fit the model pptmstr is building? Treat "coordinator" as a working name only

> Just spitballing here. With the right gating I could see a coordinator session being very
> powerful. A special card in the fleet with all other sessions paired to it. The power to spawn
> new tasks (with gating), expand and contract priviledges (with gating). But I'm also sensing
> pitfalls such as overreach, context muddling, and confusion when well defined session teams are
> needed as well where there is a feature coordinator.

## Two needs share the name

1. **Finding:** "which tab was doing this, you figure it out". This is a question over the whole
   fleet (titles, boards, digests, recent turns), and its answer is "session X, task Y".
2. **Directing:** sending leads work and following it up.

The two need different powers. Finding is much cheaper and safer, and may not need an agent at all.

## How it fits what exists

**Directing is the shape pairs-only was built to allow.** Decided 7 of
[the channel plan](2026-10-17-two-leads-in-one-window-share-a-channel.md) reaches N>2 "through a
lead that is a member of several pair channels, which is a coordinator by convention". A
coordinator is that lead. You link it to each lead it may direct, and it uses what rounds one to
three built:
- `send_to_peer`, gated by its own rung (Decided 1);
- channel decisions (Decided 3, 12);
- wakes and the K backstop (Decided 10);
- provenance on what a lead declares at its request.

**Authority stays with each lead's board.** The coordinator asks. Work enters a board only through
that board's lead declaring it, gated by that board's rung (proposal items 3 and 7, Decided 1). A
coordinator that could declare onto another lead's board would bypass both provenance and the
receiving rung.

**The topology research priced this shape** as O2-star
([r4-scenarios](2026-10-17-channel-topology/r4-scenarios.md)):
- **Costs:** a hop per exchange, extra approvals on a reviewed coordinator, and a single point of
  failure.
- **Benefit:** no evidence in this setting. The "hubs contain errors" results come from hubs that
  check work, not hubs that forward it.
- **The 2026-10-17 run:** every third party sat above or below the leads, not among them as a peer.

**Finding doesn't fit yet.** "No agent can … see a session it isn't linked to: discovery is the
link" (proposal item 1). Answering "which tab" needs a fleet-wide read, which would be a new grant:
read-only, and given by the operator.

## The missing concept: a mandate

The powerful version, a card paired with every session that can spawn work and change privileges,
needs something pptmstr doesn't have: a **scoped, revocable mandate** the operator grants.

- **Scope:** which sessions or projects it covers.
- **Powers:** read, message, propose a spawn, propose a privilege change.
- **Ceilings:** for example "may propose sessions in project P, at most STRICT, at most 3".
- **Expiry:** optional.

Anything outside the mandate is refused, not just reviewed. That is what separates a delegate
from a superuser.

| Power | What it rides on | Gate |
|---|---|---|
| A card paired with every session | links, one pair per lead (Decided 7) | linking stays the operator's act; a mandate might link in bulk |
| Directing leads | `send_to_peer`, channel decisions, wakes | the coordinator's own rung |
| Spawning tasks or sessions | new: a proposal that parks | the operator approves |
| Expanding or contracting privileges | new: a proposal to change a session's rung | the operator approves, **always**, even on AUTONOMOUS |

**A coordinator can propose a privilege change but never apply one.** It never changes its own
rung or its own links, and never proposes past its ceiling. Without that rule, a coordinator on
AUTONOMOUS could escalate itself.

## Pitfalls, and the mechanism each needs

- **Overreach:** the mandate's scope and ceilings, enforced as refusals.
- **Context muddling:**
  - The coordinator reads summaries (cards, boards, digests, NEEDS YOU), not transcripts by
    default, and pulls rather than receiving every lead's mail.
  - Its context holds routing state, not the work.
  - If it starts carrying the work, it has become a lead, and the window should say so.
- **Confusion with feature coordinators:**
  - One concept at different scopes. A feature coordinator holds a mandate over the two or three
    leads it is linked to; a fleet coordinator holds one over everything.
  - Provenance names the mandate behind every directive.
  - Two mandates' directives colliding on one lead is a decision for the operator, never a silent
    override.
- **Gate fatigue (not named by the operator):** if every coordinator act parks, the operator
  approves more than before. Ceilings are what could make bounded acts safe to auto-approve. How
  far to go is to be measured, not guessed.
- **Which instruction wins (not named by the operator):** the operator always wins. A lead should
  see the source of each instruction, and the coordinator should be told of the operator's direct
  instructions rather than discover them.

## A sequence, if it is pursued

1. **Fleet search in the UI,** over the store that already exists: session titles, task titles and
   results, digests. It's deterministic, needs no agent, and may answer "which tab" well enough to
   make the agent version a fallback.
2. **A read-only coordinator:** a mandate granting read over a scope. It answers questions.
3. **A directing coordinator:** the mandate adds messaging over links the operator grants.
4. **Proposals** for spawns and privilege changes, always parked, within ceilings.
5. **Bounded auto-approval within ceilings,** only after measuring how much gate fatigue step 4
   causes.

Each step stands alone. The test that it is a layer and not a dependency: the window behaves
identically with no coordinator present.

## Open questions only the operator can answer

- Does "you figure it out" need transcripts, or are titles, boards and digests enough?
- Should the coordinator only tell the operator which session to go to, or also act on it (message
  that lead)?
- One coordinator per window, or per project?
- The name. "The session holding the fleet mandate" describes it. "Coordinator" stays the working
  name until one is picked.

## Not checked

- Whether fleet search over the current store would answer "which tab" in practice. Nobody has
  collected the questions the operator actually asks.
- How a mandate would be stored and restored. It would be a new record kind, and the board log's
  format only grows.
- Whether a pptmstr window can show a fleet-wide card without the rail's layout changing. Nothing
  here has been drawn.
