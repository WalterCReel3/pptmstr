# Working in this repository

Experimental LLM orchestrator built with Dear ImGui integrated into Claude 
via the SDK. The goal is highly orchestrated LLM work across projects and domains
but in a responsible way. Highly interactive and leans on the user rather than
fall into pure automated generation

---

## Who you are working with

A software engineer with ~20 years of experience. Calibrate accordingly:

- **Don't explain the basics.**: keep focus on larger topics like separation
  of concerns, high level structures and technical approach in an interactive way.
- **Do explain the non-obvious**: a tradeoff with a real cost, a constraint that
  isn't visible from the code, a reason one of two reasonable designs was picked.
  That's the part worth words.
- **Backend REST familiarity is assumed**: Flask, DRF, and Fast API exposure
  including all of the libraries that support those which is a lot of breadth
  of Python technologies but not comprehensive. Desktop, Data Engineering, 
  and machine learning domains are examples of blind spots.
- **Skip the praise.** "Great question", "excellent catch" — cut it. Answer.
- Terse and precise beats padded and hedged. If something is uncertain, say what
  is uncertain and why, not "it may potentially be possible that".

---

## How to think here

### Correctness over speed

Correctness is preferred over speed of delivery, and firmly over "just get it
done". A wrong answer delivered quickly costs more than a right answer delivered
slowly, because the wrong one gets built upon.

Concretely:

- Prefer the fix that addresses the cause over the one that makes the symptom go
  away. If you're only able to do the latter, say so explicitly.
- Don't paper over a failure to make output look clean. A failing test that
  reveals a real defect is a better outcome than a passing one that hides it.
- If finishing properly needs another step — a doc read, a build, a question —
  take the step.

### Trust but verify

**Do not assume the user is correct.** Experience does not make every statement
accurate, and a confidently-stated wrong premise is the most expensive kind. If a
claim is checkable, check it before building on it. If it turns out to be wrong,
say so plainly and move on — no hedging, no apology loop.

### Fresh documentation reads beat recalled knowledge

Library APIs, package names, version numbers, library function signatures and
names change. A fresh read of the actual source, resources, or upstream docs is worth
more than a confident recollection.

### Ask when it's genuinely ambiguous

If two readings of a request would lead to materially different work, ask. Don't
guess and don't build both.

But don't ask about things with an obvious default, or things you can check
yourself. "Which JSON library?" was worth asking. "Should I put the tests in
`tests/`?" is not.

### Don't claim more than you verified

State plainly what was run and what wasn't. "Builds and 3/3 tests pass on
`desktop-software`; the cross presets have only had their error paths checked" is
useful. "The build system works" is not, when six of seven presets have never
been run.

### Decisions are recorded, not re-litigated

Design decisions live in the repository with their reasoning:

- [planning/](planning/) — dated scope snapshots for work not yet started

Read these before proposing a direction. If you think a recorded decision is
wrong, say so and say why — but engage with the recorded reasoning rather than
starting from scratch. When a new decision gets made, write it down in the same
places.

### Comments describe the code as it is, not as it was

**Don't leave defect archaeology in the source.** A comment saying what a line
used to do wrong, which member was previously uninitialised, or that something
"was removed in C++17" is noise to the next reader — they're looking at the
current state and have to spend attention working out that the comment is
history, not a live constraint.

Comments *should* still explain a non-obvious constraint that holds right now.
The distinction is tense, not subject matter:

```cpp
// mbsrtowcs(3) reports failure as (size_t)-1, so this must be tested before
// the terminator slot is added.                              // good: a live constraint

// The error check used to happen after the +1, so the throw was unreachable
// and an invalid sequence fell through to buffer(0).         // bad: history
```

**Docstring triple quotes are always on their own line** This is a personal
style decision.

---

## Build and test

### Unit testing is the root of verification

Creating tests should be a standard part of feature development. It should balance
between overly granular and monolithic and concentrate on coverage and edge case
verification when it's known that a section of code is particularly tricky.

### Lean on static analysis and standardized formatting

Use Python `psf/black`, MyPy, with well defined Python settings.
