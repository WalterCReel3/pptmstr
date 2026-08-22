# Opus 5 and work nobody asked for

**Dated:** 2026-08-21 · **Status:** research finding; nothing here is decided ·
**Found by:** five read-only board tasks — `scope-01-official` (Anthropic's own
material), `scope-02-field` (practitioner reports), `scope-03-levers` (non-prompt
controls), `scope-04-refute` (adversarial), `scope-05-systemcard` (returned
UNVERIFIED)

**This is not a planning record.** It answers one question — does Claude Opus 5
initiate work nobody asked for, and can that be controlled — and it settles nothing.
It exists so the next person does not re-run the search, and so nobody cites the
parts that did not survive it. Everything below is split into what is established
and what is not, and the split is the point of the file. A claim marked open here is
open, not merely unfinished.

Every quoted passage is verbatim from a source someone on the board opened directly.
URLs are inline so a reader can check rather than trust.

---

## The finding that frames the rest: the harness changed on the same day the model did

Opus 5 shipped 2026-07-24. On that date Anthropic published
[the new rules of context engineering](https://claude.com/blog/the-new-rules-of-context-engineering-for-claude-5-generation-models)
(Thariq Shihipar):

> We removed over 80% of Claude Code's system prompt for models like Claude Opus 5
> and Claude Fable 5 with no measurable loss on our coding evaluations.

Among the deleted text:

> In code: default to writing no comments. Never write multi-paragraph docstrings or
> multi-line comment blocks — one short line max. Don't create planning, decision, or
> analysis documents unless the user asks for them — work from conversation context,
> not intermediate files.

Its replacement:

> Write code that reads like the surrounding code: match its comment density, naming,
> and idiom.

The two most-reported Opus 5 over-delivery symptoms are comment bloat and unrequested
`.md` files. **Those are the two deleted rules.** Both presets — the long one and the
lean one — were confirmed present in the installed CLI at
`/home/wreel/.local/share/claude/versions/2.1.220`, selected by model. So a user who
upgraded inside Claude Code would see both symptoms appear with the model weights
playing no part at all.

Two consequences worth stating separately.

**The replacement rule has positive feedback.** "Match its comment density" reads the
current file. Once density rises — by any cause, including a previous session — the
instruction ratchets rather than corrects. The deleted rule had a fixed floor; this
one does not.

**"No measurable loss on our coding evaluations" is the sentence that explains the
public dispute.** Coding evals score whether the code works. They do not score
unrequested comments or files nobody asked for. Anthropic measuring no loss and users
reporting more slop are therefore *compatible*, and neither side is lying. This is the
single most useful thing to hand someone arguing either position.

---

## Established

### Anthropic names the behaviour

On
[prompting Claude Opus 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5),
under "Task scope and over-verification":

> Claude Opus 5 can also expand the scope of a task, adding steps that weren't
> requested or applying its own judgment about what the task should be.

That is Anthropic's own words about this model. The behaviour is not a community
rumour.

### Anthropic makes an efficacy claim for exactly one piece of its advice

On that same page, one recommendation carries a claim that it works — deleting
verification instructions, of which Anthropic says *"removing them reduces wasted
tokens with no loss in quality."*

The scope instruction, the length instruction, the narration instruction and the
delegation instruction carry **no claim of any kind that they have an effect.** They
are offered as example prompt text.

This has a wording consequence that is easy to lose and expensive to lose. Write
*"Anthropic documents an example instruction."* Never *"Anthropic documents the fix."*
The second sentence asserts something Anthropic did not.

### The same page contradicts its own counterweight

The scope paragraph guards both directions — it asks the model to keep the task's
shape *"rather than quietly narrowing, widening, or transforming it."* Roughly 900
words earlier, under code review, the same page says:

> If your review prompt says "only report high-severity issues" or "be conservative,"
> the model may follow that instruction literally and report less; ask it to report
> everything and filter in a separate pass instead.

So Anthropic documents both that a restraint instruction is the remedy and that
restraint instructions are over-obeyed. Someone applying the page whole gets opposing
advice; nothing on the page reconciles them.

### There are at least three failure modes, not two

Over-delivery is the one that gets discussed. Two others are reported first-hand,
concurrently, by the same practitioner on the same model — Paweł Huryn,
[Claude Opus 5: the best Opus yet, once…](https://www.productcompass.pm/p/claude-opus-5-the-best-opus-yet-once)
(2026-08-10):

| Mode | Reported as |
|---|---|
| Over-delivery | *"you ask a question and Opus 5 starts changing things. Like a psychopath. I ask 'how could we improve this?' and it starts rebuilding a feature."* |
| Under-delivery | *"it does four of the five things you asked, then reports done. Until you ask 'everything?'"* |
| Over-asking | stopping to ask permission when the decision is obvious |

They co-occur rather than trading off. That kills the intuitive mental model — the
one where the model has a single dial and you turn it down. A scope instruction
addresses one of three independent failures, and there is no reason to expect it to
touch the other two.

### Lowering `effort` is not a documented Opus 5 scope control

The sentence people cite for this is real, and it is about a different model. On the
[effort page](https://platform.claude.com/docs/en/build-with-claude/effort), under
the heading for Opus 4.7: *"At lower effort levels, the model scopes its work to what
was asked rather than doing more than requested."* The Opus 5 section of the same page
does not repeat it. What it says instead:

> Effort controls thinking volume, not visible response length: on Claude Opus 5,
> changing effort does not reliably shorten responses, so prompt for length instead.

Anthropic's only explicit statement about effort's reach on Opus 5 is a negative one.

Read that as *undocumented*, not as *disproved*. One first-hand uncontrolled account
points the other way — HN user RGS1811 on
[Elevated errors on Claude Opus 5](https://news.ycombinator.com/item?id=49068029):
*"It is aggressively proactive in ways that make it very hard to use. I had to turn
down the effort level to 'low' to stop it from going off in random directions every
couple of turns."* Named model, named lever, stated before-and-after, n=1, no control.

### The concrete over-engineering shapes belong to 4.5/4.6

Extra files, unnecessary abstractions, defensive handling for cases that cannot occur,
unrequested docstrings — these are named on the
[prompting best practices page](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices),
whose "Overeagerness" section opens: *"Claude Opus 4.5 and Claude Opus 4.6 have a
tendency to overengineer by creating extra files, adding unnecessary abstractions, or
building in flexibility that wasn't requested."* **Opus 5 is not named there.** Its own
page describes scope expansion in vaguer terms and does not list these shapes.

So "does Opus 5 over-engineer in these particular ways" is a question Anthropic has not
answered. A different model's page is what gets cited for it.

### Harness circularity is proven for delegation and not for scope

Claude Code injects an Opus-5-only delegation restriction through the `claude_code`
preset — so reports of delegation behaviour and the recommended delegation fix are
partly the same closed loop.

It does **not** ship Anthropic's published scope instruction. `"Deliver what was
asked"` — that instruction's opening — has zero occurrences in the 2.1.220 binary.
The circularity argument is real for one item and does not extend to the other.

### Custom output styles silently drop the built-in scope instructions

Unless a style sets `keep-coding-instructions: true`, the built-in scope-change
instructions are omitted. The default is `false`. Anyone who wrote an output style to
control this model's behaviour removed part of the existing control while doing it,
without being told.

---

## Not established — record as open, and do not launder into fact

**The Opus 5 system card was never opened.** There is no HTML rendering and every
fetch route exceeded the ceiling. A passage about poor calibration of task scope,
where the model over-engineers, *probably* exists — two secondary readers describe the
same substance in different vocabulary with no cross-citation between them, which is
weak but real convergent evidence. The specific wording is unattested. **Do not quote
it as Anthropic's**, and do not treat "Anthropic conceded this as a measured
limitation" as something we know. If it verifies, it becomes the only place Anthropic
reports task-scope miscalibration as an evaluation finding rather than a prompting
footnote, and that would change how much weight this whole file can carry.

**The "scope changes drop to nearly zero" figure is unsourced.** It circulates in
bundled CLI skill text. It appears on no Anthropic page and in no practitioner source
anyone found. An unsourced number that gets repeated is worse than no number, because
repetition is mistaken for corroboration.

**No controlled evidence exists for any prompt-side fix.** Not for the scope
instruction, not for the length instruction, not for a CLAUDE.md rule. The
best-instrumented public result is negative: GitHub issue #83691 stacked four
mitigations, including a hook enforcing *"answer only what was asked"*, and recorded
that the targeted errors continued after each was added. Caveats that keep it from
being a refutation: n=1, a single session, no control arm, and a second model in the
loop.

**No account of the behaviour exists on the bare Messages API.** Every report in the
public corpus comes from inside a harness. Nobody has decoupled the model from the
system prompt it usually runs under — which is exactly the measurement the first
section above makes necessary.

**The most-cited single incident is second-hand.** Reddit was unreachable from the
tooling, so it reached us through an aggregator that admits partial bot summarisation.
Grade it accordingly.

---

## A method finding, because it generalises to what this repo is building

Three times during this investigation a web-search summariser returned *real text with
the model label swapped*:

1. The effort quote above, handed back as Opus 5 guidance when it sits under the Opus
   4.7 heading. Two separate summarisers did this.
2. A fabricated instruction — "do not add adjacent features" — attributed to Anthropic.
3. A confident narrative about how "neither Claude Opus 5 arm delivered," citing a real
   Anthropic protein-design paper. The paper's campaigns were run by Opus 4.8 and
   Mythos Preview. It never mentions Opus 5. This was caught only by fetching the PDF
   and reading all 29 pages.

The failure mode is not hallucination, and that is why it is dangerous. The prose is
accurate, the quotation is verbatim, the URL resolves, and the version label is wrong.
It passes every plausibility check a reader applies by reflex. The only thing that
catches it is opening the source.

`STYLE.md` §2 already says *a probe must capture the result, not the narration*,
because a model reporting success is not evidence of success. This is that rule one
level up: **a worker reporting a citation is not evidence of a citation.** pptmstr
spawns research roles whose entire output is prose about documents the lead never
opens.

---

## Confidence, and what would settle it

Roughly **0.85** that the harness change is a major contributor to the reported
behaviour; roughly **0.5** that it dominates.

The 0.85 rests on how specific the coincidence is. The change landed on the model's
launch day, the deleted rules name the two most-reported symptoms by name, and both
presets are confirmed in the shipped binary. That is not a general "harnesses matter"
argument; it is two rules and two symptoms.

It stops at 0.5 for dominance because Anthropic names scope expansion on Opus 5's own
page, independent of the harness, so the model's contribution is not zero and its size
is unmeasured. Nobody has run the comparison, here or publicly.

What would settle it: an A/B on one repository and one task set, default lean preset
versus `CLAUDE_CODE_SIMPLE_SYSTEM_PROMPT=0` — which forces the long preset, and whose
env var string is confirmed present in 2.1.220 — scored on comments per LOC and on
unrequested files created. One repo, one task set, two arms. That measurement does not
exist anywhere we could find, which is the more interesting half of the finding.

---

## Not settled here

- Whether the system card contains the task-scope passage, and in what words.
- Whether any prompt-side instruction reduces unrequested work at all.
- How much of the behaviour is the model and how much is the system prompt around it.
- Whether the three failure modes share a cause or merely a reporter.
- Whether `effort` reaches scope on Opus 5.
- Anything about what pptmstr should do in response. This file is a finding. A
  decision belongs in `planning/`, and writing one here would be the behaviour the
  file describes.
