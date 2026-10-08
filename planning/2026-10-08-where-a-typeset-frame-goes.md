# A typeset frame is spent in Python placing glyphs that have not moved, and a streamed paragraph is set again from its first word on every token

**Dated:** 2026-10-08 ·
**Status:** measured; nothing changed in `advtext` or in `pptmstr/`. Every "would buy" below
is an **estimate read off a profile, not a result**: no change was prototyped and none was
timed. ·
**Origin:** the operator, who wrote `advtext`, 2026-10-08 — *"Losing performance but
gaining text selection and copy is a huge win. However, performance metrics reported for
advtext might be useful for optimization work."* ·
**For:** the library's author, deciding what to optimise in `advtext`. What pptmstr's seam
adds is §5 and is kept apart on purpose. ·
**Engages with:** `planning/2026-10-07-advtext-sets-the-text.md` (the adoption, and why
RICH stayed the context log's default), and in the library `docs/adoption-plan.md` §(e),
`docs/status.md` ("Per-paragraph reflow cost…"), `docs/cpp-extension.md` §3–4. ·
**Measured with:** `scripts/profile_typeset.py`; `advtext` at `a3c589a1ea` (`main`),
Python 3.11.2, imgui 1.92.9, 16 CPUs, collector thresholds (700, 10, 10); faces preset
`roboto`, pane 900 px, the build budget off so a layout is one call ·
**Machine:** loaded throughout. Load average 3.6–4.8 for the steady, first-frame, width,
close and diff runs and 4.5–8.1 for the stream and selection runs, with two other copies
of this script running beside every run reported here (§7 says why). Two full runs of the
width group agreed to 3%, and two of the first-frame group to 10%. **Read ratios within a
table; an absolute here is slow by an amount nobody measured.**

---

## 1. How it was measured, and what the instruments cannot see

Each scenario runs twice — through the library's own classes (`BareView`, `StreamView`,
`DiffView`, `build_document`, `Diff.from_texts`) and through `pptmstr/ui/typeset.py` — with
the same faces, theme, metrics resolver and palette, so an `advtext` row is the library
**as this host configures it**: ragged right, first-fit breaking, advances taken from
ImGui's baked font. It is not the library under its defaults, and nothing here says
anything about the optimal breaker.

Four passes over fresh state: wall time with the collector on; wall time with it off;
cProfile; tracemalloc. Headless: an ImGui context, frames begun and rendered to draw
data, nothing rasterised.

Three things about the instruments, because each one moved a conclusion:

- **cProfile on CPython 3.11 records no call for the constructor of a
  `@dataclass(frozen=True, slots=True)`**, and the time is in the caller's cumulative
  figure and in *no* function's own. Checked on a toy: 200,000 constructions of a
  seven-field one in a loop, no `__init__` row, the loop's cumulative time less its own
  time is the whole of their cost. Own times therefore do not sum to the time profiled.
  The script reports the difference per function as **untimed** and counts it into the
  total. In the draw it is 18% of the profiled frame, all of it under `_glyph` and
  `_RigidSpan.pen`.
- **A call into an ImGui binding is not recorded either**, but that time *is* in its
  caller's own. So `_glyph`'s own time includes `add_text` and `ImVec2`. §2 separates them
  by timing each piece alone.
- **tracemalloc sees what a body still holds when it returns**, not what it allocated and
  freed on the way. The per-glyph objects of a draw are in the second class; they show up
  as constructor time, not as bytes.

## 2. Where the time goes, by scenario

Collector-off medians unless stated. `share` is of the profiled sample, own time plus
untimed. Function names are the library's.

### A settled document, drawn again with nothing changed

| room | glyphs | ms a frame | µs a glyph |
|---|---|---|---|
| 30 lines | 1,343 | 5.3 | 3.94 |
| 60 lines | 2,621 | 10.1 | 3.85 |
| 120 lines | 5,196 | 20.1 | 3.87 |

Linear in the glyphs on screen and in nothing else. All of it is
`backends/imgui_draw.py` (91% with its untimed constructors) and `units.py:px_from_sp`
(5%); `BareView`'s own culling, decoration and bookkeeping are 1%.

| function | calls a glyph | share |
|---|---|---|
| `_glyph` | 1 | 37% |
| `_RigidSpan.pen` | 1 | 32% |
| `draw_runs` (the loop and its `match`) | — | 8% |
| `snap_px` | 2.5 | 6% |
| `px_from_sp` | 2.3 | 5% |
| `_Preambles.character` / `.bake` / `.of` | 1 each | 6% together |

The same draw taken apart, by replaying the `add_text` calls the view itself made
(60 lines of room; the other two sizes agree to a point):

| what | µs a glyph | of the draw |
|---|---|---|
| `BareView.draw`, as shipped | 3.84 | 100% |
| the same into a draw list that discards every call | 3.66 | 95% |
| its `add_text` calls alone, arguments ready | 0.19 | 5% |
| the same, an `ImVec2` built per glyph from a kept offset | 0.31 | 8% |
| one `add_text` per run of glyphs ImGui would place identically | 0.05 | 1% |

And one of each per-glyph operation, alone, net of the 50 ns call that wraps it:

| one of | ns | a glyph |
|---|---|---|
| `_RigidSpan(...)`, seven fields | 1,050 | 1 |
| `DrawStats(...)`, three fields | 530 | 1 |
| `ImDrawList.add_text`, one character | 190 | 1 |
| `px_from_sp` | 120 | 2.3 |
| `imgui.ImVec2` | 75 | 1 |
| `snap_px` | 45 | 2.5 |

**ImGui is a twentieth of the draw. Two frozen dataclasses built and dropped per glyph —
the successor span `pen` returns and the running `DrawStats` `_glyph` returns — are
1.58 µs of the 3.85, about 40%.** The profiler's 18% for the same thing is a floor: it
inflates the Python around a constructor and not the constructor.

Two facts about the output, which bound what a retained draw could reuse:

- Scrolled 63 px, 2,404 of the 2,517 glyphs the view issued were at the same offset from
  its origin as before, to a hundredth of a pixel. The rest are the lines that scrolled in.
  A glyph's snapped offset from the view's origin did not depend on the scroll.
- 81–86% of neighbouring glyphs on one baseline, in one face, size and ink, sit exactly one
  ImGui advance apart, making runs of 4.5–5.7 glyphs. That is a property of **this host**,
  which lays out with ImGui's advances; it is not a claim about the library's own metrics,
  where `docs/adoption-plan.md` Phase 4 found run batching exact only by accident of an
  even size.

The collector has nothing to do here: 3–10 young collections per 1,000 frames, no middle,
no full, zero net tracked objects a frame.

### A first frame of new text

| lines | collector off, ms | ms a line | collector on, ms | on over off |
|---|---|---|---|---|
| 100 | 61 | 0.61 | 64 | 1.04 |
| 1,000 | 477 | 0.48 | 568 | 1.19 |
| 5,000 | 2,298 | 0.46 | 2,878 | 1.25 |

At 1,000 lines the three parts, timed without the profiler: `build_document` 94 ms,
`layout_for` 457 ms, the draw 10 ms. Layout is four fifths.

| where | share at 1,000 lines |
|---|---|
| `pack.py` (`hpack` 18%, `_accumulate_glue` 5%) | 24% |
| `style.py` (`char_measure.measure` 10%) | 14% |
| `nodes.py` (`GlueTotal.of`, `GlueTotal.__add__`, 104,312 calls each) | 12% |
| C calls made by the library (`max` from `hpack`: 488,974 calls, 5%) | 11% |
| `markdown.py` (`_Converter._append_text` 6%) | 8% |
| `linebreak.py` | 5% |
| `shipout.py` | 5% |
| `markdown_it` | 5% |

Read by cumulative time the picture is simpler: **`hpack` called from
`linebreak._first_fit.fits` is 44% of the first frame and 58% of `layout_for`.** `fits`
packs the line from its start for every candidate breakpoint, so a line of *k* words is
packed *k* times at lengths 1…*k*: `char_measure.measure` ran 273,239 times over 25,440
characters, **10.7 measurements a character**. `_first_fit`'s docstring calls this "linear
in the paragraph at a given measure"; it is linear in the paragraph and quadratic in the
line.

### The same document at a width it has not had

42 ms at 100 lines, 365 ms at 1,000 — 77% of a first frame, which is all of its layout.
Nothing is reused; `UnitCache`'s docstring says a resize misses on every unit, and it
does. The profile is the first frame's without the parse.

### A stream

`sync`, `layout_for` and `draw` timed separately inside the frame (collector on):

| open block | arriving | ms a frame | parse | layout | draw | over idle |
|---|---|---|---|---|---|---|
| paragraph, 10 lines | nothing | 3.3 | 0.0 | 0.0 | 3.3 | |
| paragraph, 10 lines | a token a frame | 26.7 | 2.6 | 18.2 | 5.9 | 8.1× |
| paragraph, 100 lines | nothing | 20.8 | 0.0 | 0.0 | 20.8 | |
| paragraph, 100 lines | a token a frame | 198 | 19.9 | 145 | 31.1 | 9.5× |
| paragraph, 400 lines | a token a frame | 699 | | | | 35× |
| list, 100 items | nothing | 10.9 | 0.0 | 0.0 | 10.9 | |
| list, 100 items | a token a frame | 13.0 | 0.7 | 1.4 | 10.8 | 1.2× |
| list, 400 items | a token a frame | 14.4 | | | | 1.4× |
| fence, 100 lines | nothing | 8.3 | 0.0 | 0.0 | 8.3 | |
| fence, 100 lines | a token a frame | 10.9 | 0.2 | 1.8 | 8.9 | 1.3× |
| fence, 400 lines | a token a frame | 9.9 | | | | 1.2× |

(The 400-line rows have no parts: the paragraph's is 20 frames, wall only. Every open
block grows as the tokens arrive; the "10-line" paragraph goes from 10 source lines to
about 16 over the samples, and is called 10–16 below.)

An idle stream is a draw. A list or a fence being written costs 1.5–4 ms over idle and
barely more for being four times as long: one unit is rebuilt (`units built 1`) — the last item, or the last
piece of the fence. **An open paragraph is one block and one unit, and it is parsed, broken
and shipped whole on every token.** Its cost is linear in its length, and the breaking is
the first-fit trial packing of the section above: at 10–16 lines, `hpack` under `fits` is
half of the frame, 15.7 measurements a character.

Two smaller things in the same rows:

- The draw of a token frame costs 1.5× the idle draw of the same glyph count at 100 lines
  (31.1 against 20.8 ms). The cause was not established; the unit's commands are new
  objects every frame, and collections land inside it.
- A rebuilt live unit allocates every `Glyph` twice — once in `shipout._Shipper.hlist_out`
  and once in `ui/units.py:moved`, which copies each command to translate it. In the
  tracemalloc table the two are the top two lines, equal to the byte.

### A stream that closes

| | 98 lines | 392 lines | units built |
|---|---|---|---|
| the frame after `close()`, same `StreamView` | 7.7 ms | 8.7 ms | 0 |
| first frame of a new `BareView` over `stream.document` | 55 ms | 167 ms | 33 of 33; 126 of 132 |

`close()` itself re-lays nothing. A **second view over the very block objects the stream
has just laid out** lays every one of them out again, 7× and 19× the frame it replaces:
`UnitCache` and `LayoutCache` belong to the view.

### A diff

| `Diff.from_texts` | ms |
|---|---|
| 5,000 lines, every 50th changed, every line distinct | 80 |
| 5,000 lines, every 7th changed, every line distinct | 538 |
| 5,000 lines, every 7th changed, a 60-line unit repeated | 14 |
| 20,000 lines, every 50th changed, every line distinct | 1,579 |

99% is `difflib.SequenceMatcher` under `diff.py:_opcodes`; the library's own rows and hunks
are 1%. Four times the lines at the same density is 20 times the time. Cost follows
(changes × lines) when lines are distinct, because the matcher's recursion peels one small
match off the left each time; repeated lines are cheap because `difflib` discards them as
junk.

`DiffView`: a steady frame is 3.7 µs a glyph, the same draw path and the same shares as
`BareView`; the diff's 998 rows cost nothing a frame. A first frame at 60 rows is 10.5 ms
against 7.2 steady, in `_Builder.text` and `DiffView._info`.

### Selection and copy

| 1,000 lines, 60 of room | ms a frame |
|---|---|
| a selection dragged a line a frame | 12.4 |
| everything selected and held | 12.7 |

Both are the draw of what is in sight; `selection.py` is under 1% of the dragging frame
(`FragmentQueries.rects`, 16 calls). **Selecting costs nothing measurable over drawing.**
`selected_text()` of the whole 38,000-character document is 15–20 ms a call, 53% in
`selection.py:FragmentQueries.text`; it is paid on a copy and not on a frame. (These two
runs were at load 6–8; compare them with each other and not with the 10.1 ms above.)

## 3. Where the garbage goes

**A draw makes none.** Steady draw, idle stream, `DiffView`, a held or dragged selection:
at most 10 young collections per 1,000 frames and no older ones. The two objects built per
glyph are freed as they are made; they cost time (§2), not collections.

**A layout makes all of it.** Per call, collector on:

| scenario | young / middle / full | paused, ms | of the call | longest pause, ms |
|---|---|---|---|---|
| first frame, 1,000 lines | 130 / 12 / 1 | 92 | 16% | 82 |
| first frame, 5,000 lines | 644 / 59 / 2.3 | 476 | 17% | 224 |
| new width, 1,000 lines | 63 / 6 / 0.75 | 63 | 14% | 78 |
| token frame, open paragraph, 10–16 lines | 3.5 / 0.3 / 0.03 | 1.9 | 7% | 51 |
| token frame, open paragraph, 100 lines | 32 / 2.9 / 0.26 | 20.5 | 10% | 70 |
| token frame, open list or fence, 100 lines | 0.07–0.4 / 0.01–0.04 / 0 | 0.02–0.07 | <1% | 0.8 |

So a paragraph streamed at 100 lines runs a full collection every fourth frame, and each
one is 50–70 ms.

**What a full collection costs, and whose objects it walks.** Measured apart
(`shots/profile/trk_probe.py`, a throwaway):

| | ms |
|---|---|
| one full collection, the process holding no document | 49 |
| the same, holding one 1,000-line document and its layout | 74 |
| the same, after `gc.freeze()` | 0.00 |

Two thirds of every full pause in this process is the host's own heap — the modules,
fontTools' parsed fonts, ImGui's bindings — and not text. That part is not the library's
to fix and is in §5. The library's part is 25 ms per 1,000 lines held, and it is these
87,236 tracked objects:

| type | count | of the document |
|---|---|---|
| `shipout.Glyph` | 29,651 | 34% |
| `nodes.CharNode` | 25,792 | 30% |
| `nodes.Glue` | 10,183 | 12% |
| `nodes.GlueSpec` | 5,469 | 6% |
| `shipout.SetGlue` | 5,417 | 6% |
| everything else | 10,724 | 12% |

88% are leaf records — integers, floats and a string — that cannot be part of a cycle and
are tracked because an instance of a slotted dataclass is.

**Bytes.** A 1,000-line document and its layout hold 9.8 MB in 176,290 blocks, about
10 kB a source line. The largest lines: `Glyph(...)` in `hlist_out` 2.0 MB;
`style.font_key`'s `f"{face.value}@{size}"` **1.5 MB in 25,530 strings**, one per
character, where the document uses a handful of distinct keys; `CharNode(...)` in
`_Converter._append_text` 1.2 MB.

## 4. What the numbers say would pay most, in `advtext`

Ranked by how often the cost is paid times how much of it could go. **Every ceiling is an
estimate from the tables above and not a result.** 1 and 2 overlap, as do 3 and 4: the
ceilings do not add.

**1. Keep what a unit's draw computed, and replay it.** Per placed unit, the list of
(font, size, offset from origin, ink, character) that `draw_runs` works out is the same
next frame unless the unit, the fonts, the inks or the fractional part of the origin
changed.
*Named by:* the anatomy table — the view's own `add_text` calls replayed with an `ImVec2`
built per glyph cost 8% of the draw; 95% of the draw survives discarding every ImGui call.
*Ceiling (estimate):* about 90% of every steady frame of every view, to roughly 0.3 µs a
glyph; nearer 99% where consecutive glyphs can be joined into one `add_text`, which the
81–86% figure says is most of them **under this host's metrics only**. The estimate leaves
out what validating the kept list costs and what the selection highlight and per-range
inks need; neither was measured. `docs/cpp-extension.md` §4 reaches the same shape from the
other side — return vertices, not commands — and its 26 ms frame with 1.3–2.2 ms of ImGui
is the same ratio measured here.

**2. Stop building two frozen dataclasses per glyph.** `_RigidSpan.pen` returns a new
seven-field span for every glyph and `_glyph` a new `DrawStats`; both are frozen and
slotted, which is the slowest thing to construct in CPython 3.11 (each field goes through
`object.__setattr__`).
*Named by:* 1,050 ns and 530 ns each, alone, against 3,850 ns a glyph; the profile's
untimed column puts the same time under exactly those two functions.
*Ceiling (estimate):* about 40% of a steady frame, with no change to what is drawn. It is
the small step if 1 is not taken, and is subsumed by 1 if it is. `pen`'s docstring gives a
reason for the span being frozen ("the thing whose identity guarantees a word is drawn the
same way twice"); the estimate does not weigh that.

**3. Lay out only what a token added to a live block.** `_first_fit`'s own docstring
argues that "a finished line never changes when material is appended". Nothing uses it:
the live block is parsed, broken and shipped from its first word.
*Named by:* a token frame over an idle one — 8× at 10–16 lines, 9.5× at 100, 35× at 400 —
with `layout_for` 68–73% of the frame and the parse 10%; and by the collector table, where
this is the only per-frame scenario that reaches a full collection.
*Ceiling (estimate):* 70–80% of a token frame on an open paragraph, more as it grows, and
with it most of that row of the collector table. Nothing for a list or a fence, which are
already incremental. This is the row that decides whether a streamed paragraph holds a
frame budget: 27 ms at 10–16 lines does not hold 16.7 ms.

**4. Carry the line's width forward in `_first_fit` instead of packing it again.** One
`hpack` per candidate breakpoint, each from the start of the line.
*Named by:* 10.7 measurements a character on a first frame (15.7 on the streamed
paragraph); `hpack` under `fits` 44% of a first frame and 58% of `layout_for`.
*Ceiling (estimate):* about 40% of a first frame and about half of a width change, if a
candidate's verdict came from a running total and only the chosen line were packed; less
if `hpack`'s verdict needs more than totals (its shrink and glue-order rules were not
read closely enough to say). It also takes about half of the cost 3 is aimed at, without
3. `GlueTotal.of` and `GlueTotal.__add__` (12% of the first frame, 104,312 calls each, one
allocation a call) fall with it.

**5. Let a second view start from a layout the first one built.** A `BareView` over a
closed stream's `document` shares the blocks and none of the work.
*Named by:* 55 ms and 167 ms for the settled view's first frame against 7.7 and 8.7 ms for
the frame it replaces, with every unit built again.
*Ceiling (estimate):* 85–95% of that frame, once per closed run. A hitch and not a rate,
but it lands on the frame a run finishes, and the context log makes this swap for every
run. Whether a stream's units are valid for a settled view was not checked — live and
committed blocks carry different alignment on their style (`StreamDocument`'s docstring).

**Named by the numbers, and smaller:**

- **Leaf records the collector tracks.** 88% of a document's 87,236 tracked objects per
  1,000 lines cannot hold a cycle. Estimate: up to 25 ms off every full collection per
  1,000 lines held, and some part of the 14–17% of a layout spent paused; how much, no
  measurement here says, because the count of collections depends on survivors and not
  only on their kind. Change 1 wants a unit's glyphs in a flat form anyway.
- **`Diff.from_texts` at many scattered changes.** 99% `difflib`; 538 ms at 5,000 distinct
  lines with every 7th changed. A matcher that anchors on lines unique to both sides
  would bound it; the ceiling is the 99%, the realistic figure unknown. The seam already
  builds large diffs off the frame.
- **`style.font_key` builds a string per character.** 1.5 MB of a 9.8 MB document; one
  dictionary lookup would return the same object. Estimate: 15% of held bytes, about 2%
  of build time.
- **`ui/units.py:moved` copies every command of a rebuilt live unit**, doubling its
  `Glyph` allocations. Folded into 3 if 3 is done.

## 5. What pptmstr's seam adds on top

Seam over library, collector-off medians, same text:

| scenario | ratio | the seam's own functions, of its profile |
|---|---|---|
| steady draw | 0.99–1.05 | 0% |
| first frame, 100 / 1,000 / 5,000 lines | 1.08 / 1.09 / 1.09 | 3% |
| stream, open paragraph, token a frame | 0.91–0.99 | 2–3% |
| stream, open list, token a frame, 10 / 100 / 400 items | 1.16 / 1.16 / 1.68 | 9% / 25% / 46% |
| stream, open fence, token a frame, 10 / 100 / 400 lines | 1.47 / 1.00 / 1.04 | 1% |
| `diff_model`, `DiffView` | 0.94–1.03 | 0% |
| selection, copy | 0.92–1.06 | 0% |

Per frame the seam is a dictionary lookup. (The fence's 1.47 at 10 lines is between two
passes over different text; the collector-on medians of the same pair are 5.4 and 5.8 ms,
1.07.) Four things are its own:

- **A list being streamed costs the seam time linear in the list.** `Stream._measure`
  walks every live block on every token, and every item of an open list is live:
  `_unbreakable` runs 404 times a frame at 400 items and with `_run_extent` and
  `_PixelMetrics.advance` is 46% of the frame — 25.5 ms against the library's 15.2.
- **A dropped seam view is freed only by a full collection.** The seam's entry, its view
  subclass and the closures `typeset.markdown` hands the view form a cycle: dropping a
  `ViewCache` over a 100-line document leaves about 11,000 unreachable objects — every
  `Glyph` and `CharNode` of it — where dropping the library's own `BareView` or
  `StreamView` leaves 0. So text a pane has let go of stays in the heap, and in every
  later collection's walk, until the oldest generation is next collected.
- **`_PixelMetrics.kern` is not kept.** 21,046 calls and 3.6% of a 1,000-line first frame,
  most of it the library's `FaceMetrics.kern` underneath. `advance` and `bbox` are kept.
- **The first-frame 8–9%** is not the seam's own code (3%) but more library work on the
  blocks the seam dresses and fits; the profile does not separate it further.

Two rows of the script's own seam table compare unequal bodies and are left out above: at
a new width the seam's call also draws; and the settled view of a closed run is built from
the stream's blocks on the library path and from the text, parsed again, on the seam's.

And one thing that is neither the library's nor the seam's: the 49 ms a full collection
takes with no document held. `gc.freeze()` once pptmstr has started would take the
process's own heap out of every later collection. Not tried in the application; the 0.00
ms above is this script's process.

## 6. Checked against what was already believed

| claim | from | here |
|---|---|---|
| steady draw is about 4 µs a glyph | `scripts/bench_panes.py`, earlier builders | **agrees**: 3.85–3.94 |
| `DiffView` follows glyphs, about 3.3 µs each, not rows | library, `adoption-plan.md` §(e) | **agrees** in kind: 3.7, on a loaded machine; 998 rows cost nothing |
| ImGui's share of a frame is small (1.3–2.2 of 26 ms) | library, `cpp-extension.md` §3 | **agrees**: 5% |
| a streaming view is about 2 ms a frame over its draw | library, `adoption-plan.md` §(e) | **agrees for a list or a fence** (parse and layout 2.0–2.1 ms at 100 lines); **wrong for an open paragraph**, 21 ms at 10–16 lines and 165 ms at 100 |
| a `StreamView` re-lays its live block on every token, 6.7 ms against 4.9 idle | this session | **confirmed**, and the size of it is the live block's: that pair is a short block |
| closing a run re-lays blocks the stream had just laid out | this session | **half**: `close()` re-lays nothing; the settled view that replaces the stream re-lays everything |
| a stream at a token a frame runs about 120 young / 11 middle / 1 full per 300 frames | this session | **consistent**: between the list rows and the 10–16-line paragraph row here |
| one full collection over a held document is about 50 ms | this session | **the number agrees and the reading does not**: 49 ms of it is the process with no document |
| a watched 20,000-line typeset stream took 69 full collections, median 109 ms | this session | **not reproduced**; no scenario here is that large. The mechanism — full collections scale with live-block re-layout, each one walking the whole heap — is what §3 shows |
| a first layout is about 0.3 ms a line | this session | **disagrees**: 0.46–0.61 here. Load, or a different text; not settled |
| `Diff.from_texts` is about 270 ms at 5,000 scattered lines | this session | **bracketed**, 80–538 ms by density of change; not the same fixture |
| `Diff.from_texts` is 62 ms at 5,000 lines with every 7th edited | library, `adoption-plan.md` §(e) | **disagrees ninefold on distinct lines** (538); a file with repeated lines is 14. The library's fixture was not read |
| `register_font` clears every metric cache | this session | **confirmed by reading** `fontlib._registry_changed`; its cost was not measured |
| the seam measures advances from ImGui's baked font | this session | **confirmed by reading** `typeset._PixelMetrics`; it is also why runs are exact (§2) |

## 7. What was not measured

- **No GPU, no rasteriser, no swap, no frame pacing.** A millisecond here is Python plus
  ImGui's CPU-side draw-list work. Whether 8,000 single-glyph `add_text` calls cost the
  renderer more than 1,500 joined ones is not known.
- **No quiet machine.** Two earlier copies of the script could not be stopped from inside
  the sandbox (its commands do not share a process table, and the tool that stops a
  background task needs an approval this unattended session cannot get) and ran beside
  every run reported. Absolutes are high by an unknown amount.
- **No change was made or prototyped.** Every ceiling in §4 is arithmetic on a profile.
- **The seam was a moving target.** `pptmstr/ui/typeset.py` was being edited by another
  task of this branch while these runs were taken (5,463 lines when the first run
  started, 6,027 uncommitted when the last ended), so §5 describes the working tree of
  that hour and no commit. The two findings a later edit could most easily have removed
  were run again against the tree as it stood at the end: the open-list ratio (1.71, the
  seam's own functions 46%) and the cycle (about 11,000 unreachable objects) both stood.
  The library did not change: `a3c589a1ea` throughout.
- **Only this host's configuration.** First-fit, ragged right, ImGui's advances, the
  `roboto` preset, 900 px, one text (the fake fleet's report, renumbered so no block
  repeats). The optimal breaker, justified text, math, tables as a workload of their own,
  and the library's own metrics were not run.
- **CPython 3.11 only.** The constructor cost in §4.2 and the profiler's blind spot in §1
  are properties of that interpreter.
- **Unequal samples.** A frame scenario has 300 samples (100 for streams), a 5,000-line
  build has 3. The 400-line paragraph row is 20 frames with no profile.
- **`µs a glyph` on a stream row** divides one pass's time by another pass's glyph count,
  and the passes' texts differ; treat it as approximate there.
- **The application.** Nothing here ran `python -m pptmstr`; panes, their caches and their
  budgets are `scripts/bench_panes.py`'s subject.

## 8. Running it again

```
.venv/bin/python scripts/profile_typeset.py --only steady anatomy --out shots/profile/steady.txt
.venv/bin/python scripts/profile_typeset.py --only first width close diff select --out shots/profile/layout.txt
.venv/bin/python scripts/profile_typeset.py --only stream --sizes 10 100 --frames 100 --out shots/profile/stream.txt
.venv/bin/python scripts/profile_typeset.py --only stream --sizes 400 --frames 20 --no-profile --no-alloc
```

About two minutes, twelve, eight and three. The default (`--only` everything, every size)
includes the 400-line paragraph through all four passes and is most of an hour for that
row alone. Output is printed at the end of a run and not before, so a run that is killed
leaves nothing: run groups separately. The header of every table file carries the load
average before and after.
