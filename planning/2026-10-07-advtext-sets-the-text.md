# advtext sets the text in five panes, and the old renderers are still there

**Dated:** 2026-10-07 (built 2026-10-07 to 2026-10-08) ·
**Status:** built on `adv-text-experiment`; an experiment, not a merge candidate until
a person has looked at it (§3); the last fix round's outcome is in §10 ·
**Origin:** the operator, 2026-10-07 (§1) ·
**Reverses, in part:** three decisions in
[`archive/2026-08-10-transcript-markdown.md`](archive/2026-08-10-transcript-markdown.md)
and one policy in `pyproject.toml` (§4) ·
**Takes up:** [`2026-09-08-the-toolkit-was-never-the-constraint.md`](2026-09-08-the-toolkit-was-never-the-constraint.md)
§6, by adoption (§5) ·
**Read with:** [`2026-10-07-font-survey.md`](2026-10-07-font-survey.md) and
[`2026-10-07-which-fonts-ship.md`](2026-10-07-which-fonts-ship.md), which hold the font
decisions and are not repeated here ·
**Authoritative for the surface and its costs:** the module docstring of
`pptmstr/ui/typeset.py`. Where this record and that docstring disagree, the docstring
is the tree and this is the summary.

**The branch moves DETAIL, the NEEDS YOU expansion, the context log, BOARD and BRIEF
onto the layout library `advtext` (`/home/wreel/Source/imgui-adv-text`), behind one
module and one setting. Nothing was deleted to do it: `Settings.typeset` off is every
pane as it was. The session ran unattended; apart from the three operator messages
quoted in §1, every decision below was an agent's, and no person has seen a frame of
the result.**

How to read the attributions: "measured by" means a builder ran it and reported the
number to the lead; "read-derived" means a reviewer with no shell read code and PNGs.
The writer of this record re-checked what the tree can show (constants, names,
docstrings, test names, the two bench outputs named in §7) and nothing else. §11 lists
where the tree disagreed with what was reported.

---

## 1. What was asked

The launch premise, verbatim in substance: develop an experimental branch that uses
the work in `/home/wreel/Source/imgui-adv-text`, with these ideas "but not limited to"
them —

- Detail pane display of markdown
- Description field renderings of parked concerns
- Diff display in the detail pane and parked concern
- Streaming text (defaulted to markdown) in the context log
- Updated board rendering using well crafted typesetting and layout
- Long description text in concern text rendering between agents

— and to ideate other applications "for giving pptmstr a more \"professional\" look
with its user interface without sacrificing the \"density\" guidelines for what is
essentially a professional developer's tool."

Mid-session the operator asked whether font selection could be made viable, then said:
*"I would be more keen to ship with fonts that are bundled already but even checking
installed in order to find an open font we could bundle ourselves may be worthwhile."*
and asked whether the work was bottlenecked behind one builder. That is the whole of
what a person said.

## 2. What was built

Scope, from `git diff HEAD --stat` at the gate: 42 files, 24,478 insertions, 345 deletions.
`pptmstr/ui/typeset.py` is 5,463 lines and `tests/test_typeset.py` 4,402; the five
panes changed by about 2,450 lines between them.

In order, by board task:

| task | what is now true |
|---|---|
| `env` | the build declares `advtext`; a screenshot can be taken from a sandbox with no reachable display (§3) |
| `fixtures` | the `--fake` fleet carries text worth typesetting: 7 approvals, 2 questions and a failure, a board `t1`..`t8`, concerns `fake-c1`..`c8`, a brief with a superseded entry, a settled markdown report, a wall-clock stream |
| `keys-guard` | a review letter key is the bare key only (§8) |
| `diff-texts` | a parked approval carries the texts its diff was made from (§6) |
| `baseline-bench` | each text pane's frame cost is measured before the renderer changes (§7) |
| `seam` | one module is where pptmstr meets advtext |
| `font-survey`, `fonts` | see the two font records |
| `detail`, `inbox`, `transcript`, `board-pane`, `brief-pane` | the five consumers |
| `seam-fixes`, `fix-inbox`, `fix-detail`, `fix-transcript`, `fix-board-brief`, `seam-polish`, `adopt-approval-panes`, `adopt-transcript` | two rounds of review findings, fixed |
| `seam-final` | nothing in a fenced diff or a glob is hidden from an approver; §10 |
| `approve-gate` | no approve path sends a call whose body is still being typeset; §10 |
| `gate` | the tree is green after the writes have stopped; §11 |

Reviews: `plan-audit`, `review-seam`, `review-panes`, `review-final`. All four were
read-derived. Reviewers can open PNGs and cannot run anything.

Brief is the lead's addition to the operator's list. Considered and not boarded: the
launcher's prompt preview, the health pane, the splash — inputs or non-prose.

## 3. The environment this was built on, and what that excludes

**No package index was reachable.** `./.venv` is a composite the lead built by hand:
`/usr/bin/python3.11 -m venv --without-pip`, plus `_composite.pth` listing this
worktree, advtext's source checkout, advtext's venv `site-packages` (imgui-bundle
1.92.900, fontTools, numpy) and the main pptmstr venv's `site-packages`
(claude-agent-sdk, anthropic, pytest-asyncio, PyOpenGL). `black`, `mypy` and `pytest`
are shell shims and `ruff` is a symlink into advtext's venv. Nothing on this branch
has run in an environment `pip` resolved.

**`bootstrap.sh`'s new path has not been run end to end.** It now requires
`ADVTEXT_SRC` (default `$HOME/Source/imgui-adv-text`) and installs it editable before
`-e .[dev]`. Only the refusal path — checkout missing — was executed.

**The imgui-bundle floor is advtext's.** Main's venv has 1.92.801, which lacks
`ImDrawListFlags_.text_no_pixel_snap`; advtext's draw sets it. `pyproject.toml` now
says `imgui-bundle>=1.92.900` and `tests/test_env_floor.py` pins the attribute, not
the number. Nobody watched 1.92.801 fail; the floor is argued from changelogs and the
flag's presence.

Baseline on the composite before any change, run by the lead: lint clean, mypy clean,
2569 passed / 3 xfailed.

**Every window was Xvfb over TCP in a user and mount namespace, drawn by Mesa
llvmpipe at 1x** (`scripts/xvfb_shot.sh`). `DISPLAY=:1` is unreachable from the
sandbox and `xvfb-run` hangs there, because the sandbox refuses `AF_UNIX`.
`scripts/screenshot.py` now reads `GL_BACK` in `before_swap`; `GL_FRONT` gave an
all-black PNG under llvmpipe. That the same read is right on a GPU is argued from the
GL specification and was not run.

So: no GPU, no LCD, no HiDPI, no real pointer. Drag-select, Ctrl+C, the wheel, menu
clicks and the feel of the 50 ms stream throttle were never exercised by a person.
**No person has looked at any of it.** The only judgement of how it looks is one
reviewer's, from PNGs, some of them taken from intermediate builds.

Two things outside the worktree, for the operator to know about:

- A stale `bisect.py` in the shared `$TMPDIR`, another session's, shadowed the stdlib
  module for two builders' throwaway scripts and launched headless Firefox against a
  scratch profile. No process was left and the worktree was untouched.
- 755 stale `advtext-fonts-*` temporary directories from earlier runs remain. The seam
  now removes its own at exit (`_remember_scratch`); the ones already there are not
  this session's to delete.

## 4. Three recorded decisions and one policy are reversed, in part

All four are in or beside
[`archive/2026-08-10-transcript-markdown.md`](archive/2026-08-10-transcript-markdown.md).
None of that record's reasoning was wrong for what it could see; each reversal is a
premise that changed.

**"Hand-rolled rendering, not `imgui_md`" — the hand-rolled renderer stays, as the
other arm of the experiment and as the fallback.** The August argument was against
`imgui_md` specifically: it aborts without four faces, re-parses every visible block
every frame, exposes no rule hooks, and sizes table columns from the first row.
advtext is not `imgui_md` and has none of the first three; it degrades by role, caches
layout by block value, and its parser is reachable (privately — §6). So the August
decision is not so much overturned as no longer the only alternative on offer. What it
protected is kept: `rich_pane`, `blocks`, `inline` and `span_layout` are untouched,
RICH is still a mode, and `typeset.enabled()` false — setting off, library absent,
faces failed — is the old renderer whole. The lead took that decision so that the
branch can be judged by flipping one setting, and because a 5,000-line seam nobody has
looked at is not something to make load-bearing.

On tables, the August deciding factor: ImGui's native table still sizes columns better
than advtext does for a table too wide for its pane, where the first column is
squeezed (§9, open). The point that decided against `imgui_md` has not been won by
its successor.

**"Copy-block replaces text selection" — typeset views select and copy; copy-block
remains.** The August record chose copy-block because draw-list text was not
selectable and called sub-paragraph selection "what is genuinely lost". advtext hit
tests its own glyphs, so that loss is recovered in every typeset view, with one
selection across all views at a time. Copy-block is not removed: it is the only copy
in RAW, WRAP and RICH, and it still yields verbatim source where a selection yields
what was laid out (the seam takes its own inserted line breaks back out, `_selected`).
The 2026-09-08 record's wall — source markdown from a partial selection — is not
climbed; a selection copies rendered text.

**"Emphasis is a colour shift, not a slant" — reversed for the prose presets, stands
for `mono`.** The August reason was that no Inconsolata italic exists and the pane
would not leave Inconsolata. The first half is still true; the second is what changed.
Prose in the default preset is Roboto, whose four faces already ship in imgui-bundle,
so italic is a real italic at no vendoring cost. In `mono` italic is upright, because
neither the disk nor advtext's role table has a monospace italic. The font survey
argues this at length and its §8 puts it to the operator as a question.

**"Nothing at runtime opens a font file" — reversed.** That sentence was in
`pyproject.toml`'s dev-dependency comment and justified keeping fontTools dev-only.
advtext reads faces through fontTools at run time, so `fonttools>=4.50` moved to the
runtime dependencies. The cost is measured: 1.1 to 1.8 s in `theme.load_fonts` at
start-up with the math face, 0.34 s without it, nothing when `Settings.typeset` is off
(the faces are then loaded on the first switch-on, 1.11 s once).

**"Underscore emphasis is disabled" — kept, not reversed.** advtext already disables
`_` emphasis; the transcript builder confirmed it by a run, and
`test_identifiers_keep_their_underscores_and_dollars_are_not_math` in
`tests/test_transcript_pane.py` holds it. The August trade (`_italic_` does not
italicise) therefore carries over unchanged, and so does its warning that someone
will try to "fix" it.

**"Setext headings and link reference definitions are excluded permanently" — half
kept, half reversed, and the reversed half was nobody's decision.** Run by the gate
through the seam in headless frames, settled (`typeset.markdown`) and streamed a line
at a time (`typeset.stream`), reading each view's blocks after every line
(`shots/gate/setext_probe.py`, output beside it; machine-local, gitignored):

- *Link reference definitions: kept.* The seam reads with advtext's
  `references=False` parser in both paths. `See [the doc][d].` is set literally,
  brackets and all; `[d]: https://example.invalid/x` is set as a paragraph of its own
  with the URL on screen; `[d][]` and `[d]` likewise. Nothing is swallowed and a
  late definition changes no earlier block.
- *Setext headings: not excluded.* `A paragraph` / `---` / blank / `next` is a level-2
  heading and a paragraph, settled and streamed, with no rule drawn (`===` gives
  level 1). In the stream the first line is set as a paragraph on the frame it
  arrives and **is set again as a heading when `---` arrives**: the revision of text
  already shown that the August record excluded the construct to prevent. No word is
  hidden by it -- only the underline is consumed -- so the gate left it. It reaches
  the screen only in the context log's TYPESET mode, which is not the default; RICH
  goes through `blocks.py` and still has no setext. Switching `lheading` off on the
  parser the seam already patches (`_reader`) would restore the invariant, and is
  open (§12).

## 5. The September record's open direction was taken by adoption, and its measurement was taken first

[`2026-09-08-the-toolkit-was-never-the-constraint.md`](2026-09-08-the-toolkit-was-never-the-constraint.md)
§6 left one direction open: "a stronger text widget in `pptmstr/ui/`, in Python,
against this application's real workload, before deciding whether it is ever
extracted." What happened is the reverse order. The widget was built outside the tree
first, as advtext, and adopted here. The operator asked for exactly that, so this is
not a departure to defend — but §6's reason for building in-tree was that the widget
"must own the height model" for *this* workload, and the consequences of that not
having been the design input are visible: the seam is 5,000 lines of host-side
accommodation, it reaches past the library's facade in four places, and the one pane
§6 was written about — the context log — is the one where the adopted widget did not
earn the default (§6).

§6's collapse of three modes into one did not happen. There are now four modes. TYPESET
is the first that wraps, colours by kind, selects and culls at once, which is the
capability §6 wanted; RAW, WRAP and RICH remain because TYPESET holds about 1,000
lines and has no find-in-text.

§6's "`Run` is the wrong shape" is moot for typeset views and still true of
`rich_pane`, which nobody touched.

**"Take the measurement first" was done** (`baseline-bench`, `scripts/bench_panes.py`),
and it answers the question §6 posed:

- The O(total lines) slice `cache.lines[:stable]` is 1.1 ms a frame at 100k lines.
  Real, small, and not what RICH costs.
- RICH idle is 4.2 / 4.3 / 6.0 ms at 1k / 10k / 100k lines; its first frame is
  17 / 137 / 1,283 ms. The incremental parser is what makes RICH affordable, so
  preserving it matters. That was the thing to settle.
- The pane docstring's claim that a frame with nothing new is free was false for the
  line modes: RAW idle is 0.14 / 0.89 / 13.7 ms, of which at 100k `_visible()` is
  2.75 ms and the eager `copy_text` 7.5 ms. The September record's §7 had flagged
  `_visible()` by reading; this is the number. The docstring now says so.

§6's "before touching any of it: nothing in `tests/` covers `_draw_lines`,
`RenderMode` or the WRAP path" was not addressed as such. `tests/test_transcript_pane.py`
grew by 1,620 lines, on the TYPESET path and the mode default.

§7's note that `review.SHORTCUTS` has one occurrence in the tree, its own definition,
is still true.

## 6. Decisions, who took them, and on what

**One seam.** Only `pptmstr/ui/typeset.py` imports advtext, and a test reads the
imports (lead). Panes hold a `ViewCache` each; one view, one pane (plan audit). The
reason is in the module docstring's first paragraph: what a text engine needs from a
host is small and easy to get five different ways in five panes.

**`Settings.typeset` is the A/B instrument, and it ships on.** The old renderer is
also the degraded mode (lead). With the setting off at launch no faces are loaded;
with advtext absent the application starts and typesetting is unavailable with a
stated reason (`seam-fixes`). Note that the default in `settings.py` is `True`: on
this branch every pane is typeset until the operator turns it off. Whether that is
the right default per pane is an open question (§12).

**Proportional prose by default, monospace for code, diffs and literal text; ragged
right; mathematics not parsed; body at the live UI font size** (lead). Ragged because
the library has no hyphenation and justified text without it is loose. Math off
because `$PATH=$HOME` is a shell line. The math *face* is loaded anyway, because
Roboto has no `→` or `✓` and the fallback would otherwise be read mid-session at
most of a second. Presets `roboto`, `mono`, `roboto-large`, `droid` and a text-size
multiplier are switched live with no atlas work (fonts builder; the lead recommends
dropping `droid`). Bundle nothing — see the survey.

**The seam measures with ImGui's hinted advances, not advtext's font-file metrics**
(seam builder, who named it the decision it was least sure of). The first screenshot
showed word gaps of 1 to 7 px in Roboto and monospace words 5% wide: Inconsolata at
16 px advances 7.63 in the file and 8 as drawn. The resolver the seam supplies asks
ImGui. The cost is that sub-pixel kerning is discarded (pair kerns are kept, to the
whole pixel) and that pptmstr's typeset text is not what advtext's own examples draw.
It is a finding for the library as much as a decision here (§13).

**Nothing written is hidden: raw HTML and images are text.** advtext, following
CommonMark, drops raw HTML and images with their text; an unclosed `<!--` swallows
the rest of a document. For an approval surface that is a defect of a different order
than a rendering wart: a concern body can show its approver two words. The seam
builder first fixed it by escaping the source. `review-seam` showed, by reading, that
the guard had holes (an already-escaped `\<`, a stray backtick, a phantom third span,
a fence in a container), and the general argument against it is in the docstring: an
escaper is a second markdown reader that must agree with the real one everywhere.
`seam-fixes` removed the guard and instead switches off `html_block`, `html_inline`
and `image` on advtext's own parser, through the **private**
`advtext.markdown._parser(False, False)`, process-wide, and adds a core rule so a
link's destination is on screen as `text (https://...)`. `review-final` traced every
`MarkdownIt` construction in advtext and could not find an unpatched path. That is a
read, not a test; a runtime self-check is one of `seam-final`'s items. If the private
name goes, `load_fonts` fails and nothing is typeset — the old renderer.

**Diffs are built from texts the record carries, never from a disk read in the UI and
never from `pending.diff`.** The plan audit overturned the lead's first specification,
which had the seam read `raw_args` and the disk: two diffs of one approval could then
disagree, and the UI thread would read files. `approval.diff_texts` is now the one
place that decides what a call does to a file; the driver runs it once at park time
and `PendingApproval.texts` holds `DiffText`s. `render_diff` was rebuilt on it and is
byte-identical over an 8,911-call battery (diff-texts builder). Three things that
builder and the reviewer reported and that remain true:

- The joined `-foo+bar` line reproduces whenever a text lacks a closing newline — the
  fallback, `MultiEdit`, a file with no final newline, an unterminated `Write` — and
  not in an ordinary mid-file `Edit`.
- `DiffText.will_not_apply` is the gate's prediction and is unverified against the
  real Edit tool.
- A non-unique `Edit` anchor is shown as a clean replacement (read-derived).

**"May not apply" is the pane's own wrapped line, once, under both renderers, with
`diff(warning=False)`.** The lead instructed that the pane's line be dropped in favour
of the diff header's. Three builders in turn refused, each with screenshots: under a
long path the header's copy is cut at the view's edge and cannot be scrolled to. The
lead's instruction was wrong and the tree records the builders' answer.

**DETAIL.** Live narration is not typeset (detail builder: 3 to 5 ms a frame for an
ordinary paragraph and 39 ms for a 400-word one, against 0.24 native). Settled
markdown is bounded at `_SETTLED_LINES` = 2,000 with a "set all N lines" button. Prose
wraps; diffs scroll sideways. The pane's module docstring still opens "the whole of
the thing under the cursor, with nothing clipped"; for a diff that is now "nothing
unreachable", which is narrower, and is a question for the operator (§12).

**One body classifier, `typeset.prose_parts`.** Bodies are not promised to be markdown
— a concern or a task detail is whatever an agent wrote. Three panes had grown three
rules and one body read differently in two of them (`review-panes`); there is now one,
and a consumer must not decide for itself.

**The context log has a fourth mode, and the default with the seam on is RICH, not
TYPESET** (lead's rule, transcript builders' measurement). The rule: TYPESET is the
default only if its streaming median is within about 2 ms of RICH and a long watch
leaves no recurring frame over about 30 ms. After `adopt-transcript` the median gap is
1.5 to 1.6 ms at 1k and 10k lines, which passes. Over a 20,000-line watched stream
TYPESET had 83 frames over 30 ms (max 34.5; RICH 0, max 7.85), and 69 full garbage
collections at a median 109 ms and max 134 against RICH's one of 48 ms. That fails.
`gc.freeze()` after start-up brings the pause to 59 ms at the median, and holding 400
lines instead of 1,000 to 35 ms, at the same frequency: no constant in the pane's
reach fixes it. The numbers are on `DEFAULT_MODE`'s docstring and in
`shots/at/watch_final_typeset.txt` and `watch_final_rich.txt` (machine-local,
gitignored).

The operator asked for the log "defaulted to markdown". RICH is markdown, so the
letter of the request is met by the renderer that already existed, and the typeset one
is a radio button away. Whether that meets its intent is the operator's to say.

**advtext is declared by bare name and installed from `ADVTEXT_SRC`** (lead). A
`file://` direct reference would commit one machine's absolute path, and a relative
one assumes a sibling checkout that a git worktree is not.

## 7. What was measured

**How.** `scripts/bench_panes.py` (`--typeset on`, `--only <pane>`): bare ImGui frames
with no window and no rasteriser, `perf_counter_ns` around each pane's own draw
function. CPU milliseconds inside the draw, not a frame time, and nothing about a GPU.
The script's own instruction applies to every figure here: **read ratios, not
absolutes.** Text does not repeat; the library caches layout by block value and a
repeated paragraph measures about a fifth.

Not every number came from that script. The 20,000-line watch, the build-budget
stalls, the DETAIL narration cost, the board relayout and the diff-model timings came
from throwaway drivers under the gitignored `shots/` (`shots/at/watch.py` and
others). STYLE.md §2 says a runtime claim in a design doc has a script in `scripts/`
that produced it. Those do not, yet (§8).

**Baseline, old renderers** (`shots/bench_panes_baseline_1.txt`; re-read by this
record's writer):

| | median ms |
|---|---|
| transcript RICH idle, 1k / 10k / 100k lines | 4.2 / 4.3 / 6.0 (first frame 17 / 137 / 1,283) |
| transcript RAW idle | 0.14 / 0.89 / 13.7 |
| return to a pruned 5k-line node | RAW 4.1, RICH 61 |
| DETAIL: 60-line Write / 5,000-line Write / 970-line deliverable | 0.11 / 1.46 / 4.85 |
| inbox, one approval expanded | 0.07 |
| board, collapsed / all expanded | 0.19 / 0.25 |
| brief | 0.075 |

**Typeset on**, as reported by the pane builders on the final tree where known:

| | median ms |
|---|---|
| the seam | about 4 µs per glyph on screen, every frame |
| brief | 3.2 to 3.6 |
| inbox | about 2.2 |
| DETAIL (same three) | 2.2 / 2.2 / 4.84 |
| board, collapsed / all expanded | 0.83 / 6.2 |
| transcript TYPESET idle | 5.1 to 5.3 |
| transcript TYPESET streaming, 1k / 10k / 100k | 5.9 / 5.9 / 6.7 (p95 9.6 to 10.4); RICH 4.2 / 4.4 / 6.7 |
| return to a pruned node | TYPESET 9.1, RICH 67 |

The price is plain: a pane that cost a tenth of a millisecond costs two to six, and
several panes are on screen at once. The gain on the same axis is the return to a
node (9 against 67) and that a long diff no longer scales with its length.

**Stalls the build budget removed** (seam-fixes, seam-polish, adopt builders):

| | before | after |
|---|---|---|
| 5,600 lines of markdown, first show | 2,309 ms in one frame | about 72 frames, max 47 ms |
| width change, 600 / 2,000 / 5,600 lines | 343 / 1,106 / 3,191 ms | worst frame 50 / 55 / 73 ms (76 / 162 / 348 when a full collection lands) |
| diff model, 5,000 lines, on the frame | about 500 ms | 24 to 99 ms with the model on a thread |
| diff model, 20,000 lines | 8.0 to 8.7 s | 172 to 177 ms |
| board relayout on a session flip | 35 to 109 ms | under 5 ms |

**Bounds, each announced on screen:** `_TYPESET_WINDOW` 400 lines per advance and
`_TYPESET_KEEP` 1,000 lines held in the context log; `_SETTLED_LINES` 2,000 in DETAIL;
a 6 ms build budget per frame (`set_budget`) after a free allowance of `_FREE_LINES`
80 lines under `_FREE_MS` 30; a stall that cannot be divided is announced one frame
ahead (`_DIFF_ANNOUNCED_LINES` 2,000, `_ANNOUNCED_LINES` 400); the open run's stream
is read every `_STREAM_EVERY_MS` 50.

**Density** (`review-panes`, counted from PNGs, some from intermediate builds):

- transcript prose: 272 px typeset against 588 px WRAP for the same text;
- board: all 16 fixture rows in about 535 px of about 750, against 8 tasks and 6
  concerns filling the pane; at FOCUS width 16 rows against 8 tasks and 3 concerns;
- inbox diff: 23 file lines against about 21;
- inbox expansion heights, on / off: Write 608 / 608, Bash 228 / 212, Edit-absent
  328 / 298, concern 556 / 608, declare 484 / 580.

So the density brief is met where there is prose, and narrowly lost on two short
expansions.

## 8. What the reviews found, and what happened to it

**Fixed**, each reproduced by a builder before the fix unless noted:

- Ctrl+A approved the focused call and Ctrl+Shift+A the agent's whole queue, because
  a view that handles its own chords captures no keyboard. Nine failing hazard cases
  were reproduced on a real headless ImGui context; `review.handle_keys` now binds the
  bare letter only (`tests/test_review_keys.py`). Escape is deliberately unguarded.
  `app._handle_layout_keys` has the same guard, added by `seam-fixes` and present in
  the tree.
- HTML and images dropped; link destinations hidden (§6).
- A parked Bash command cut off with no way to reach the rest — **in both renderers,
  and on main**. Now whole, exact, capped.
- Unbreakable runs unreachable in a window that cannot scroll; `document()` going
  stale on a list mutated in place; scroll and selection lost on rebuild; an empty
  diff drawn as a view; a stream withholding a trailing character; no cost guard.
- `missing == 0` not meaning every character drew: the library substitutes U+FFFD at
  build time and then counts it as a glyph. `ViewCache.replaced` is the second count.
- Seam state leaking between test files. A segfault was seen once. Draws now raise
  outside a frame and faces belong to one context; five and then three full runs were
  clean. One segfault with no captured cause is not a closed finding.
- A watched transcript growing without bound.
- Tables with no row rules; a fence's info string hidden; the warning cut off in the
  diff header; box-drawing lines not joining; dimming not reaching tables and diffs.

**Not fixed:**

- Inline code reads smaller than the Roboto prose around it. `roboto-large` is the
  workaround, not a fix; a span has its block's size in the library.
- A table too wide for its pane squeezes its first column; a headerless fenced diff
  carries two rows of chrome. Both the library's.
- The inbox question preview is still native raw markdown with cut lines.
- The native sub-agent notice clips with typeset off. On main too.
- `review.SHORTCUTS` is unreferenced (§5).
- Garbage-collector pauses (§6).
- U+FFFD for emoji and CJK, in both renderers.
- Several measurements have no script under `scripts/` (§7).

## 9. What is not verified

Beyond §3, which is the large one:

- `bootstrap.sh` end to end; any environment `pip` resolved.
- The parser patch at runtime. Its completeness is a reviewer's trace.
- `will_not_apply` against the real Edit tool.
- Anything interactive, by a person.
- The scale multiplier under BOARD and BRIEF at anything but 100%; HiDPI at all.
- The August record's streaming exclusions were checked by the gate (§4): reference
  definitions are honoured, setext headings are not. Checked on those inputs in
  headless frames, not held by a test.
- The baseline and final benches were not re-run by the writer of this record; two
  output files were read.
- The suite on the final tree is in §11, run by the terminal `gate` task after every
  other task had finished.

## 10. Final round (`seam-final`)

**Finished after the rest of this record was written. The list is what it was asked;
the outcome follows it, appended by the lead from the builders' reports.**

It was asked to reproduce, each with a failing test first, and then fix these —
every one read-derived by `review-final`:

1. **Must-fix:** prose written inside a bare ```` ```diff ```` fence is hidden. Rule
   asked for: draw a fence as a diff only when every line is accounted for, otherwise
   as code.
2. Emphasis eats glob stars (`**/*.py`).
3. Approve is not gated on a half-built body.
4. The background diff worker respawns on failure.
5. A width re-break restarts on every text change.
6. A wall-clock branch in the tests.
7. No runtime self-check on the parser patch.
8. Table rules fail silently.
9. A punycode link lookalike.
10. Context-address reuse in two test files.

Items 1 and 3 are the same class as §6's HTML finding: text an approver releases
without having been shown.

### Outcome

All ten reproduced on the pre-change files, as reported by the `seam-final` builder,
and all ten are fixed. Two were predictions the reviewer could not run, and both were
real:

- **A fenced diff hid prose.** Each of the four variants drew the changed rows and
  nothing else. A fence is now drawn as a diff only when the parsed model accounts for
  every line of the fence by count and content, and as code under its info string
  otherwise. Tested over 76 generated fences, settled and streamed a line at a time.
  The rule is strict: a fence carrying `diff --git` or `index` lines is set as code,
  because the hashes and the mode are on no row, so a diff pasted from `git diff`
  never gets the diff view. Loosening that is one acceptor in `_accounted`.
- **Emphasis ate characters.** `delete **/*.py and tests/**/*.md` was set as
  `delete/*.pyandtests//*.md`, and `2*3*4` as `234`. The fix is a rule on the parser
  (`_written_stars`), not an escape in `prose_parts`: a star run with a letter or
  digit on both sides, or part of a glob, is text. Text written exactly as emphasis
  (`rm *tmp and old*`) still loses its stars; that is markdown read as markdown.

The rest: a failing background diff worker raised out of `inbox.draw` on every frame
and now fails once, says so, and hands the pane back to the arguments; a width
re-break completes while the text grows; `load_fonts` reads a probe through
`build_document` and `StreamDocument` and refuses to make typesetting available if a
word of it is missing, so the private-parser patch can no longer land on an unused
object unnoticed; an empty table row is ruled and a rule mismatch is counted in
`ViewCache.unruled`; a link whose text is a Unicode lookalike of its punycode
destination shows the destination.

**Item 3 took a second task (`approve-gate`).** `seam-final` could make a half-built
body visible -- the "typesetting: N more lines" notice is drawn above the body and the
inbox's `approve` button is held -- but the keyboard and the batch paths live in
`review.py`. `review.withheld` is now the one place that decides and every approve
path asks it: `a` is refused for a call no pane has drawn and for one whose body was
short at its last draw; Shift+A and both "approve all" forms send nothing if any body
in the batch is known short, and still send rows that were never expanded, which is
the batch design the confirm dialog exists for. Reject and edit never ask. A refusal
is said on the row, in DETAIL, or beside the batch buttons. Seven of the nine tests
that could run against the old code failed on it with the call in `sent`.

One behaviour changes with typesetting off: `a` is refused for a row no pane has drawn
yet, which is one frame.

### What this round leaves

- "Whole" means typeset, not read. A body inside a capped scrolling child counts as
  whole.
- Shift+A on a never-drawn focused row approves it and its siblings, one modifier away
  from the `a` that now refuses.
- A long path in a fenced diff's header is cut at the view's edge and cannot be
  scrolled to.
- While a second file's `--- path` is the last line to have arrived, a streaming
  fenced diff flips to code and back.
- Accounting for a fence grows with it: 5.3 ms a frame at 500 lines against 3.3.
- `ViewCache.unruled` is read by tests only.
- Nothing in this round was run in a window with a person at it. The suite was run
  three times after each of the two tasks: 3252 and then 3278 passed, 3 xfailed.

## 11. Where the tree disagreed with what was reported

- **`pyproject.toml`'s comment on `fonttools` said advtext takes "every glyph advance
  and kern from the font file through fontTools rather than from ImGui".** In pptmstr
  the seam overrides exactly that: advances are ImGui's (`pixel_metrics`), only pair
  kerns are the file's. The dependency is right and the stated reason was half stale.
  The gate rewrote the comment to say what the file still supplies.
- **`README.md` had no section naming a renderer.** One was added, with the
  `ADVTEXT_SRC` requirement that `bootstrap.sh` now enforces.
- The `fonts` task's write list names `scripts/font_survey.py`. The script is
  `scripts/survey_fonts.py`.
- `fix-transcript`'s specification speaks of `_TYPESET_WINDOW = 200`. The tree has 400,
  with the measurement that chose it on the constant.
- The module docstring gives the width-change worst frames as 52 / 53 / 74 ms; the
  figures reported to the lead were 50 / 55 / 73. Different runs; the docstring is
  cited in §7 only where it agrees to the precision that matters.
- `Settings.typeset` defaults to `True`. The open question was posed as "should
  typeset be on by default per pane"; as built it is one switch and it is on.
- STYLE.md was read for a rule that stopped matching and none had. The mismatch runs
  the other way — §2's script rule is right and this branch is short of it (§7) — so
  STYLE.md is unchanged.

### The gate

Run by the terminal `gate` task on 2026-10-08, on the composite venv of §3, with
every other task finished and nobody else writing. The gate's own edits were the
`pyproject.toml` comment above, one sentence of the README and this record. The
five suite runs came after the first two and before this section was written; a
sixth, after it, gave the same count. Outputs are under `shots/gate/`
(machine-local, gitignored).

```
$ make lint
All checks passed!
All done! ✨ 🍰 ✨
133 files would be left unchanged.

$ make typecheck
Success: no issues found in 47 source files

$ make test
3278 passed, 3 xfailed in 77.92s (0:01:17)
```

- `make test` five times in a row, each to its own file, exit status 0 each time:
  3278 passed, 3 xfailed in every run (77.9, 74.2, 71.7, 73.6, 73.6 s). No segfault
  and no fatal-error line in any output. The baseline before the branch was 2569
  passed, 3 xfailed. Seven clean runs in all, counting one before the gate's edits
  and one after, do not explain the segfault §8 records; they are what was seen.
- `make typecheck-all`, which is outside `check` by design: 1008 errors in 48 files
  (133 checked). Not worked on.
- `scripts/verify_typeset.py` under `scripts/xvfb_shot.sh`: PASS, every check `ok`
  in four theme and face combinations; the gui callback over 129 frames was median
  11.2 ms, p95 26.3, max 45.7 under llvmpipe.
- `scripts/bench_panes.py`, once each way, 300 frames a scenario, median ms inside
  the pane's draw, typeset on against off:

  | | on | off |
  |---|---|---|
  | DETAIL: 60-line Write / 5,000-line Write / 970-line deliverable | 2.19 / 2.21 / 4.74 | 0.11 / 1.40 / 4.67 |
  | inbox, one approval expanded, 60 / 5,000 lines | 2.03 / 2.04 | 0.07 / 0.07 |
  | board, collapsed / all expanded | 0.64 / 4.66 | 0.19 / 0.24 |
  | brief | 3.21 | 0.07 |
  | transcript TYPESET idle, 1k / 10k / 100k | 5.24 / 5.34 / 5.34 | no such mode: the pane is in WRAP |
  | transcript TYPESET streaming | 5.90 / 6.03 / 6.68 (max 62 / 20 / 70) | — |
  | transcript RICH idle | 4.27 / 4.36 / 5.76 | 4.25 / 4.28 / 5.67 |
  | transcript RICH streaming | 4.31 / 4.53 / 6.64 (max 4.7 / 5.0 / 7.5) | 4.30 / 4.47 / 6.57 |
  | return to a pruned 5k-line node | TYPESET 9.3, RICH 66.6 | RICH 66.7 |

  These agree with §7 to the precision §7 claims. The streaming maxima are the long
  frames §6 gives as the reason RICH is the default.
- Setext headings and reference definitions through the seam: §4.
- Seven screenshots of `--fake` with typesetting on, `shots/final-*.png`, each opened
  by the gate: the application starts and every pane draws.

## 12. Open, for the operator

1. Is Roboto prose inside Inconsolata chrome the look you want? This is the question
   the whole branch turns on, and nobody qualified to answer it has seen it.
2. Should typesetting be on by default, and per pane? As built `Settings.typeset` is
   one global switch, it is on by default, and with it on the context log opens in
   RICH, not TYPESET (with it off, WRAP, as on main). The reviewer's read: BOARD and
   BRIEF yes; the inbox and DETAIL once the must-fixes have landed (they have, §10);
   the context log no.
3. Do you accept a sideways scroll for diffs in DETAIL, against the pane's "nothing
   clipped"?
4. Keep or drop the `droid` preset and the text-size multiplier? At 125% typeset text
   is 20 px beside 16 px chrome.
5. Is 2 to 6 ms per visible pane per frame acceptable, with several panes visible?
6. Should the application manage the garbage collector — `gc.freeze()` after start-up,
   or collecting at idle? It is the one thing between TYPESET and the context log's
   default, and it is an application-wide decision, not a pane's.
7. Should advtext grow the host switches in §13, so the private-parser patch can go?
8. Should the inbox's diff cap be in diff rows, which scale with text size, or in UI
   lines?
9. Should board rows fall back to sender only below about 420 px?
10. Does "defaulted to markdown" mean RICH is enough for the context log (§6)?

11. Should the seam switch setext headings off, so a streamed paragraph is never set
    again as a heading (§4)?

The font records carry their own questions and they are not repeated: survey §8,
which-fonts-ship §7.

## 13. For advtext's owner

The same person, in another tree. Each of these is something the seam works around.

- A host switch for the `html_block`, `html_inline` and `image` rules, and for
  showing a link's destination. pptmstr patches a private parser for both.
- `DrawStats.missing` is blind to build-time U+FFFD substitution, and
  `replaced_characters` / `ReplacedChar` are not in `__all__`.
- No monospace-italic role.
- Font-file metrics against ImGui's hinted advances (§6). The library lays out from
  one and draws with the other.
- A fence's info string past its first word is not drawn; nor is a diff fence's
  preamble or any line its reader does not account for.
- Inline code collapses runs of spaces.
- `SELECTION_OWNERSHIP` is not in the facade.
- A patched math font is written to a new temporary directory on every launch and
  never removed.
- `Diff.from_texts` on the frame path, with no way to divide it.
- Adding fonts between frames under a real renderer worked, in one run, where the
  library's docstring says unchecked.
