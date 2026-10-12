# The seam is re-adopted, not ported

**Dated:** 2026-10-11 ·
**Status:** the plan the re-adoption tasks on the board work from. **Everything
in §1 to §5 is from reading, by pptmstr's reviewer (task `SEAM-MAP`); nothing
was run.** A builder who finds a line here false by a run corrects the line and
says so; the run wins ·
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
| `available`, `unavailable_reason` | `typesetter.ready()`, `reason()`, `trouble()` |
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
`_held_table`, the scratch-directory bookkeeping, and the build budget with its
pieces and width debounce. The last is conditional: its only basis is advtext's
own 33 ms for `sample.md` ×10 under load, so it goes when a parse is timed in
pptmstr and not before.

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

The three reaches past the old facade:

| reach | on the new layer |
|---|---|
| reader rules (HTML and images as text, a glob's stars kept, a link's destination shown) | mechanically still possible through the private `advtext.markdown_rules._parser(references, math)`, with traps: a `str` given to a view parses with `references=True`, a stream with `False`, and they are different cached parsers; advtext's stream invariants were never tested with rules switched off. Request P7. |
| count of characters set as U+FFFD | no counterpart. Request P9. |
| breaking a word wider than the measure | no counterpart and nothing to reach; the library lets it overhang. Request P10. |

## 3. Fonts

Expressible: a role's slot (a `Theme`'s block kinds each carry a face); a size
step per block role; the diff's row pitch; registering the vendored bold and
Droid Sans with `register_font` **before** `typesetter.load_fonts()`; leading
as a ratio (`BlockStyle.leading`; `interline=` is a `TypeError`).

Not expressible, each a request in `HANDOFF-advtext.md`: hinted advances (A2);
a size for inline code runs (P11); horizontal scroll in place of wrapped code
(P12); one line pitch whatever is on the line (P13); the `ImFont` of a face
for native labels (P14); loading fonts again after start-up (P17).

To check by a run, unverified: whether `Theme.spacing` (every space one width)
reaches the library at all; whether `typesetter.load_fonts()` is safe between
frames after start-up.

## 4. The fallback

Every pane decides before it draws: `typeset.enabled(pane)` false, its own
renderer. Read as intact today in all seven places (`detail`,
`transcript_pane`, `inbox`, `review` through `state.body_shown`, `board_pane`,
`brief_pane`, `settings_dialog`), which the suite's 2,930 passes corroborate.
`theme._load_typeset_faces` swallows every exception, so a reason reaches
nobody.

**New on this layer:** a view can raise `TypesetUnavailable` from inside
`draw()`, when the pane is already in its typeset branch with a child window
open. The seam catches it inside every draw call, says the reason at the
cursor, returns a height, and has `enabled()` read `typesetter.ready()` so the
next frame is the pane's own renderer. One frame may show both.

## 5. Shape and order

`pptmstr/ui/typeset.py` becomes a package of the same name with the same
public names, so panes and `settings.py` change little:

| module | holds |
|---|---|
| `roles.py` (no advtext import) | the role catalogue, panes, families, scales, settings readers, `enabled` |
| `parts.py` (pure) | `Kind`, `Part`, `prose_parts`, `multiline_kind` |
| `gate.py` | availability and its reason, font loading, unload, reader rules, `font`, `label` |
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
