# The seam is re-adopted, not ported

**Dated:** 2026-10-11 ·
**Status:** the plan the re-adoption tasks on the board work from. **§1 to §5
are from reading, by pptmstr's reviewer (task `SEAM-MAP`), except the lines
marked "by a run"**, which the builder of step 0 (`SEAM-0`) corrected or
settled on 2026-10-11, with advtext `38f5fac`, the pair built by `make deps`,
headless and under Xvfb with llvmpipe. A builder who finds a line here false by
a run corrects the line and says so; the run wins ·
**Origin:** advtext's layout core was replaced by imtypeset under this branch
(`HANDOFF-advtext.md`; in advtext's repository `HANDOFF-pptmstr.md`) ·
**Read against:** advtext `38f5fac`, imtypeset `c9f8902` ·
**Supersedes for scope:** `2026-10-07-advtext-sets-the-text.md` (kept for its
reasoning about what a pane wants of text; its mechanism is gone).

`A/` is advtext's repository, `I/` imtypeset's.

---

## 1. What maps, what stays, what goes

**Maps onto a name advtext offers:**

| pptmstr's seam | advtext now |
|---|---|
| `markdown()` over `BareView` | `TypesetView(document, *, theme, palette, on_link, hand_cursor, copy_as, style_links, separator, math, references=True, host_for)`; `.draw(*, fonts, palette, width_px) -> bool`; `.set_document(str \| blocks)` |
| `Stream` over `StreamDocument` and `StreamView` | `MarkdownStream(theme, *, math, refuses, live_theme, converter_spaces)`; `TypesetStreamView(stream, *, palette, on_link, …, follow=True, host_for)` |
| `diff()` over `DiffView` | `TypesetDiffView(diff, theme, *, layout, pixel_height, px_per_inch, syntax, height_cap, expand_step, copy_removed_lines, copy_as, palette)`; opens its own capped child |
| `diff_model`, `approval_diff` | `Diff.from_texts`, `Diff.from_unified`, unchanged |
| fenced diffs | `TypesetDiffFences(...)` as a view's `host_for` |
| `available`, `unavailable_reason` | `typesetter.ready()`, `reason()`, `trouble()`. **By a run:** `ready()` stays true after the ImGui context its fonts are on is destroyed without `unload()`, and a kept view then draws without raising; the seam keeps the context the load happened in and compares (`gate.can_draw`) |
| `load_fonts`, `reset` | `typesetter.load_fonts(faces=None)`, `typesetter.unload()` |
| per-part ink | `view.set_colours(block, [(start, end, colour)])` |
| selection, hit tests | `view.selected_text()`, `under_mouse`, `block_under_mouse()`, `block_text(n)` |

**pptmstr's own policy, which survives with no advtext name in it:** the role
catalogue (`Role`, `Slot`, `RoleStyle`, `DEFAULTS`, `SLOTS`, `STEPS`,
`LEADING`), `Pane` and the pane switches, the families and scales, `configure`
and the settings readers, `enabled` / `pane_on` / `wanted` / `obtainable`,
`Kind`, `Part`, `prose_parts`, `multiline_kind`, `ViewCache` as a key-to-view
holder, `Weight`, and the rule for which fences are honest diffs
(`TypesetDiffFences.accepts` takes any info string that begins with `diff` or
`patch`; pptmstr requires exactly the word and every line drawn).

**Existed for the old core and goes:** `_PixelMetrics`, `_LineMetrics`,
`pixel_metrics`, `METRICS`, the `RoleTheme.font_for` subclass (the new reader
calls `face_for`), the node reaches (`_nodes`, `_broken`, `_with_breaks`),
`_held_table`, the scratch-directory bookkeeping, the deferred font load
(`load_fonts_later`, `hello_loader`: **by a run** the load is 21 ms in the
application, where it was 1.1 s, so the fonts are loaded at start-up whatever
`Settings.typeset` says), and the build budget with its pieces and width
debounce. The last is conditional: its only basis is advtext's own 33 ms for
`sample.md` ×10 under load, so it goes when a parse is timed in pptmstr and not
before. Step 0 left it unbuilt and parked its tests under the step that times a
parse. **By a run (`SEAM-1`, 2026-10-12, headless, load average 2.5):** reading
`sample.md` (112 lines) and making its view is 3.7 ms and the first draw 1.8;
×10 (1,120 lines) is 32.5 ms and 3.0; a frame after that 0.07. BRIEF, BOARD and
the settings samples hand over bodies bounded far under the first figure, so
`views.py` builds nothing over frames. An approval body and a transcript are
not bounded: the tests stay parked for the step that adopts those and times
its own content. **By a run (`SEAM-2`, 2026-10-12, `scripts/profile_seam.py`,
headless, load average 0.4): the budget goes.** Settled markdown is 15.5 ms
for 400 lines and 65.9 ms for 2,000 on its first draw, 0.09 ms a frame after,
and 6.7 ms for a new width at 2,000 lines; a reply arriving 40 characters a
frame, read again every frame, is a median of 0.70 ms (p95 1.17) with 400
lines held and 1.86 ms (p95 3.09) with 2,000. No frame of a stream needs
pacing, and the one cost left is the first draw of a very long text, linear
and paid once: four frames at 2,000 lines, which DETAIL already asks before
exceeding. The 25 parked tests are deleted, `views.pending()` and its read in
`app.py` with them. What `ViewCache.unbuilt` answers now is the one way text
handed to the seam is not on screen: the library stopped inside its draw.

## 2. A view takes markdown or advtext's own blocks, not a host's description

`TypesetView.set_document` parses a `str` and otherwise expects advtext's
parsed blocks; `MarkdownConverter.describe` knows `MarkdownText`,
`MarkdownCode`, `MarkdownRule`, `MarkdownTable`. Anything else, a
`BlockDescription` included, becomes an empty 16 px host block **silently**.
A stream view refuses `set_document`.

So each pane hands advtext **markdown source** where the text is markdown, and
**`MarkdownText` / `MarkdownCode` built by the seam** where it is literal
(`plain()`, tool output, arguments): no parser, no reinterpretation. Those two
classes are importable by module with "no promise attached"; that they are
needed is request P8. pptmstr's own block segmenter and inline tokeniser
(`ui/blocks.py`, `ui/inline.py`) feed only pptmstr's RICH renderer and stay
there; the typeset seam never used them (decision: the lead's).

**As built (`SEAM-1`), and it differs.** Monospace set by the library does not
keep its columns (`HANDOFF-advtext.md`, A2 and P21, by pptmstr's measurement),
so text that is not markdown is not handed to the library at all: ImGui draws
it, in the chrome's font at the role's pixel height, between the library's
views (`themes.native`, `views._native`). No `MarkdownText` or `MarkdownCode`
is built and the P8 import is not made. A markdown part is read with the
library's public `parse_markdown` and its blocks handed to the view, so that
consecutive parts of different heading levels share one view; a fence's info
string, which the library keeps and does not draw, is set as the first line of
its code.

The three reaches past the old facade:

| reach | on the new layer |
|---|---|
| reader rules (HTML and images as text, a glob's stars kept, a link's destination shown) | mechanically still possible through the private `advtext.markdown_rules._parser(references, math)`, with traps: a `str` given to a view parses with `references=True`, a stream with `False`, and they are different cached parsers; advtext's stream invariants were never tested with rules switched off. Request P7. **By a run (`SEAM-1`):** unguarded, `a <b>x</b> and List<String>` is set as `a x and List`, `rm **/*.py and t/**/*.md` as `rm /*.py and t//*.md`, and an unclosed `<!--` takes every paragraph after it. The reach is made, in `gate._reader` only, on the one parser `_parser(False, False)`: the seam reads every settled text with `references=False` (`gate.read`), which is the parser a stream uses, so the first trap does not arise. A probe reads through the library's public calls, settled and streamed, and the library is not offered to draw with when it comes back short. **By a run (`SEAM-2`):** streams are drawn with the rules on, and the library's stream invariant holds under them at every piece of a reply cut anywhere (`test_a_reply_cut_anywhere_reads_at_every_piece_as_it_would_whole`). A fourth rule is off, `entity`: `&amp;` was set as `&`. |
| count of characters set as U+FFFD | no counterpart. Request P9. |
| breaking a word wider than the measure | no counterpart and nothing to reach; the library lets it overhang. Request P10. **By a run (`SEAM-1`):** a 109-character path at a 300 px measure is one line 750 px wide, and the window has no scroll range to it. The seam does not break it; it tells the window how far the text runs (`views._reach`, from `set_lines`), so a pane whose window scrolls sideways reaches the end. A fenced line, by contrast, **is** wrapped by the library at the measure (P12 asks for scroll or clip). |

## 3. Fonts

Expressible: a role's slot (a `Theme`'s block kinds each carry a face); a size
step per block role; the diff's row pitch; registering the vendored bold and
Droid Sans with `register_font` **before** `typesetter.load_fonts()`; leading
as a ratio (`BlockStyle.leading`; `interline=` is a `TypeError`).

Not expressible, each a request in `HANDOFF-advtext.md`: hinted advances (A2);
a size for inline code runs (P11); horizontal scroll in place of wrapped code
(P12); one line pitch whatever is on the line (P13); the `ImFont` of a face
for native labels (P14).

**By a run:** `typesetter.load_fonts()` between frames after start-up is safe
and not free. In a hello_imgui window, called from `pre_new_frame` at frames 5,
12 and 20 with another file registered for `Face.PROSE`: it answered true, a
view kept across it and a view made after it both drew, 11 ms a call. Each call
left seven more fonts on ImGui's atlas (8, 15, 22, 29) and nothing takes them
off. The seam loads again for a change of proportional family and for nothing
else (`gate.pre_frame`); P17 is now that cost, not whether the call is safe.

To check by a run, unverified: whether `Theme.spacing` (every space one width)
reaches the library at all.

## 4. The fallback

Every pane decides before it draws: `typeset.enabled(pane)` false, its own
renderer. Read as intact in six places (`detail`, `transcript_pane`, `inbox`,
`review` through `state.body_shown`, `board_pane`, `brief_pane`). **By a run,
the seventh does not ask that question:** `settings_dialog` draws its samples
whenever `typeset.available()` is true and the setting is on, and has no pane
to ask about. So `available()` is the operator's question (the library can
draw *and* some pane is adopted) and `gate.can_draw()` the library's; with no
pane adopted the menu and the dialog offer no switch and say why.
`theme._load_typeset_faces` hands anything raised to the gate as the reason.

**Step 0 as built:** `roles.ADOPTED` names the panes whose typeset branch is
built on the library, and is empty; `enabled(pane)` is false for a pane not in
it. A step adds its pane after the run that shows it. The first pane added
makes `available()` true, and the dialog's samples then call `markdown`,
`plain`, `document` and `label` at once: all four are built before any pane is
added, or the dialog is guarded first.

**New on this layer:** a view can raise `TypesetUnavailable` from inside
`draw()`, when the pane is already in its typeset branch with a child window
open. The seam catches it inside every draw call, says the reason at the
cursor, returns a height, and has `enabled()` read `typesetter.ready()` so the
next frame is the pane's own renderer. One frame may show both. **As built
(`SEAM-1`):** `enabled()` keeps its answer until the frame is over
(`gate.stopped`, taken up by `gate.pre_frame`), because a pane asks it at more
than one place in a frame; so the frame the library stops in shows the reason
wherever its text would have been and neither renderer's half.

**Step 1 as built:** BRIEF and BOARD are in `roles.ADOPTED`, and the settings
dialog draws its samples through the same calls.

**Step 2 as built (`SEAM-2`):** DETAIL and INBOX are in `roles.ADOPTED`.
`stream()` is a `MarkdownStream` under a `TypesetStreamView`, made by the draw
and fed there, so a pane appends where it holds its text and nothing raises
outside the seam's catch. `diff()` is a `TypesetDiffView` **that no pane
calls**: by `scripts/measure_typeset_diff.py` its rows are drawn in steps of 7
and 8 px against a baked 8, rows disagree at a column by up to 13 px, and a
press on a drawn glyph is answered as a neighbouring character for 40 of 64
glyphs of a row, so an approval's diff is each pane's own renderer under
either setting, and a fenced `diff` is the code of its document
(`TypesetDiffFences` is not used; `is_diff_fence` and `accounted` stay, for the
day it is). What the library sets in these two panes is markdown: a
deliverable, a turn, a question's prose and preview, the prose argument of a
parked call. For that last one, the approval body, three things are held by
test in each pane: every word of a body built to be hidden is in what was
handed over, a run the library cannot break is inside the scroll range of the
window it is in, and an approve is not sent in the frame the library stops
under the body. The inbox row's field names and values are ImGui's chrome
font: the proportional face has no arrow or tick and a native line has no
fallback.

## 5. Shape and order

`pptmstr/ui/typeset.py` becomes a package of the same name with the same
public names, so panes and `settings.py` change little:

| module | holds |
|---|---|
| `roles.py` (no advtext import) | the role catalogue, panes, families, scales, settings readers, `ADOPTED`, `enabled` |
| `parts.py` (pure) | `Kind`, `Part`, `prose_parts`, `multiline_kind` |
| `gate.py` | `can_draw`, `available` and the reason, font loading, unload, `pre_frame`; later reader rules, `font`, `label` |
| `themes.py` | role to `Theme` and `Palette`, line heights |
| `views.py` | `ViewCache`, `markdown`, `document`, `plain`, `stream`, the catch-and-say wrapper |
| `diffs.py` | `diff`, the models, the fence policy |

About 2,300 lines against 6,088, some 850 of them moved.

Order: the gate and fonts; then `brief_pane`, the settings dialog's previews
and `board_pane`; then `detail`, `inbox` and `review`; then `transcript_pane`.
`views.py` and `diffs.py` are written by more than one step, so the steps are
a chain and not a fan.

**A pane's own renderer is not deleted by any step** (`HANDOFF-advtext.md`,
P6): it is the fallback, and on macOS it may be the product.
