# How reactive figures are today: the bar a direct vector path has to beat

Board task `reactivity-measure`. Nothing in `pptmstr/` was changed: the script
wraps `FigureWidget.draw`, `FigureWidget._raster`, `Renderer.submit`,
`Renderer.lay_out`, `Renderer.poll` and the host's texture upload in its own
process and reads what they did each frame.

**Command.** Every number below is from one run on 2026-10-21, exit 0:

```
XVFB_SHOT_SCREEN=1600x1400x24 scripts/xvfb_shot.sh -- \
    .venv/bin/python shots/reactivity/measure.py
```

The raw output is `shots/reactivity/results.json`, with grabs beside it
(`n1-shrink-drag-end.png` against `n1-shrink-sharp.png` shows the stretched
texture drawn while the pane is resized). `/shots/` is gitignored, so those
files are not in the tree. An earlier run of the script, before the wide figure
and the frame split were added, gave consistent first-paint and theme
numbers (svg image cold 359–487 ms, warm 70–81 ms; no re-raster on a theme
switch).

**Setup.** The real host, render child (resvg_py 0.5.0, merman) and
`typeset.markdown` in a 1500x1350 hello_imgui window. The reply is the
`shots/fence-backdrop/check.py` reply without its math fence: an svg fence
(natural 320x90), a flowchart (360x460) and a sequence diagram (512x330). The
resize runs add a 1400x300 svg, so that one figure is wider than every pane
width tried. Each trial puts a new nonce into every figure's text, so nothing
held from an earlier trial is found. There are 5 trials of each kind, or 4 of
each resize, so spreads are given as min–max, not as percentiles.

**Hardware caveat.** Xvfb with Mesa llvmpipe on an AMD Ryzen 7 PRO 4750U
(16 threads), at density 1. A frame took about 18 ms whatever was drawn. Of
that, the frame's own CPU work (pre-frame, NewFrame, our GUI, ImGui's render)
was about 1.5 ms. The rest is llvmpipe presenting to Xvfb, so **a figure's
latency in frames turns into milliseconds at about 18 ms a frame here**. On
a real GPU at 60 Hz the figure is 16.7 ms, and at 120 Hz it is 8.3 ms. In
probe R2 the operator's machine rendered about 1.6 times slower than this
one. Nothing here was run at density 2. There, a raster has four times the
pixels, which means about four times the memory and more render time.

## Summary

| what | today (median, min–max) | where the time goes |
|---|---|---|
| commit → first outline | **frame 0, every figure, every trial**. The commit frame's GUI took 3 ms (2–4) against 0.4 ms steady | heights come from the mark (`height_for`), so the outline appears at once |
| commit → image, warm child | svg **75 ms** (72–78, 4 frames); flowchart **147 ms** (143–148, 8); sequence **196 ms** (189–206, 11) | a serial child: layout, layout, raster, raster, raster |
| commit → image, cold child | svg **410 ms** (373–440); flowchart **491** (446–511); sequence **536** (482–565) | starting the child: the first answer came at 355 ms (315–386), against 38 ms warm |
| re-hosted, everything held | sharp in **0 frames** (5/5) | `_Textures` keyed by content |
| shrink pane → first figure sharp | **7 frames, 123 ms** (6–7 frames, 120–233 ms) | `STABLE_FRAMES` (6) + 1 frame for the pump to collect the result |
| shrink pane → all four sharp | **13 frames, 228–240 ms** (223–384) | the debounce, then four serial rasters |
| grow pane, figure over its natural width | **7 frames, 140 ms** (132–178) | as above, for that one figure |
| grow pane, figures under it | **0 frames** | width is capped at natural, and that raster is held |
| back to a width already rendered | **0 frames, 0 requests** | held |
| drawn meanwhile | the old raster, stretched: **minified up to 3.9x** (drag 888→251), **magnified down to 0.33x** (251→700) | no mipmaps (T1) |
| requests during a drag | **0**, then exactly one per figure whose width changed | the debounce coalesces as designed |
| theme switch | **0 re-rasters, 0 layouts, 0 frames not sharp** in 10 switches | constant card (`BACKDROP`), textures keyed by content, not by theme |
| theme switch, CPU | `apply_style` **5 ms and 18 ms the first time** to each theme, **0.1–0.4 ms** after that | not figure cost |
| per-frame cost of K figures | GUI **+0.029 ms per figure** (0.20 ms at K=0 → 0.66 at K=16); ImGui render +0.018 ms per figure | one rect, one clip, one `image` call each |
| texture memory | w·h·4: **115 KB** (320x90), **662 KB** (360x460), **676 KB** (512x330); upload 0.11 ms median (max 0.58) | density 2 is 4x |
| textures left over by resizing | **50 textures, 13.6 MB** after 16 resizes of 4 figures | every settled width is kept until the 256 MiB budget forces it out |

## First paint

**The outline is never late.** Every figure drew at frame 0 of every trial,
cold or warm: an svg figure as an outline at its real height, and a mermaid
one at `PLACEHOLDER_HEIGHT` until its layout came back. The commit frame's
GUI pass took 3 ms (2–4), against 0.4 ms for a steady frame. That covers
reading the markdown and sanitising the svg fence on the frame thread. The
mermaid fences reached their real height at 38 ms and 57 ms warm, which is
the second jump `host.py` describes.

**What a warm request costs, from the warm timeline** (each result's time
less the one before it): flowchart layout 35 ms, sequence layout 19 ms,
svg raster 20 ms, flowchart raster 70 ms, sequence raster 57 ms. The
round trip measured from `submit` to `poll` was 71 ms (69–74) for the svg,
106 ms (106–108) for the flowchart and 140 ms (130–147) for the sequence
diagram. Those trips include the time each request waited in the queue.

**Head-of-line blocking, measured.** Both `lay_out` requests went out
*before* the svg's `submit` in every trial (2.3 ms, 2.4 ms, then 2.8 ms). A
mermaid layout is asked for from `height_for`, in the layout pass, while a
raster is asked for from `draw`. So the 320x90 svg figure, which needs one
20 ms raster, came up at 75 ms: it waited behind two layouts it does not
depend on.

**Cold costs about 340 ms more.** The first answer arrived at 355 ms
(315–386) against 38 ms warm. That is the cost of spawning an interpreter
and importing resvg_py, Pillow, fontTools and merman (`render.START_TIMEOUT_S`
puts the start at "about a second", and here it was a third of that). The
child is started lazily, by the first request, so the first figure in a
session pays it.

## Resize

A drag here changes the child window's width once a frame, either in one
frame or over 30. Pane widths ran from 900 down to 251–324 and back up to
700–770, and every target width was new to that run so that no raster was
already held.

- **Nothing is requested during a drag.** Zero submits came in 16 drags, and
  exactly one per figure whose width changed came after. `STABLE_FRAMES`
  works as designed.
- **First sharp is `STABLE_FRAMES` + 1 frames.** That is 6 frames of
  debounce, the submit, and the result collected at the next pump, and the
  render itself fits inside that last frame for small figures. 7 frames
  came to 123 ms here.
- **The rest is the serial child.** The four figures came in at 7, 10, 12 and
  13 frames, which is one raster after another. With N figures on screen,
  the last one is sharp after the debounce plus the sum of N renders.
- **What is drawn meanwhile.** The last raster, stretched to the new
  rectangle. In a one-frame shrink the 1400 px figure was drawn minified
  3.9x for 13 frames. T1 found labels break up at 3x minified, and
  `n1-shrink-drag-end.png` shows its label broken. On a grow the same figure
  was magnified 3x (ratio 0.33), so blurred, for 7 frames. The
  least-minified figure in each shrink was at 1.24–1.42x, which is legible.
- **Width is capped at natural, so most grows are free.** Once the pane is
  wider than a figure's natural width, that figure's width stops changing.
  Its natural-width raster is still held, so growing back costs nothing. In
  this reply only the deliberately wide figure ever needed a new raster on a
  grow.

## Theme switch

Ten switches between dark and light produced no re-raster, no layout and no
frame in which any figure was not sharp. The figure's pixels do not depend on
the theme (`host.py`: the `BACKDROP` card is the same in every theme, and the
rasters are keyed by the sanitised SVG's sha). The cost is `apply_style`:
5.3 ms and 17.7 ms the first time to each theme, then 0.1–0.4 ms. One switch
frame took 84 ms and another 64 ms. In both, the measured parts of the frame
(pre-frame 0.2–18 ms, GUI under 1 ms) do not account for the time, so the
rest went to present/swap or the collector. I have not attributed it further.

## Per-frame cost of K figures

All K figures were on screen in every frame (checked: `figures_drawn_per_frame`
equals K). These are steady frames after the figures were sharp, 140 frames
each:

| K | GUI ms, figures | GUI ms, same fences as `text` code | ImGui render ms, figures | code |
|---|---|---|---|---|
| 0 | 0.197 | — | 0.16 | — |
| 1 | 0.287 | 0.255 | 0.19 | 0.23 |
| 4 | 0.361 | 0.297 | 0.24 | 0.40 |
| 8 | 0.458 | 0.354 | 0.31 | 0.63 |
| 16 | 0.655 | 0.471 | 0.45 | 1.11 |

A figure on screen costs about 0.03 ms of GUI time a frame and 0.02 ms of
render time. That is cheaper to render than the code block it replaces, and
both are noise against the 18 ms llvmpipe frame. The GPU-side cost of one
textured quad was not measurable apart from the present here.

## Memory

A texture is w·h·4 bytes at the drawn width times the density (`_Raster.nbytes`).
For the reply at natural width that is 115 KB, 662 KB and 676 KB, so 1.45 MB
for three figures at density 1 and about 5.8 MB at density 2. Uploads took
0.11 ms median and 0.58 ms at most, over 90 uploads up to 1400x300.

**Superseded widths are not let go of.** `_Textures` evicts by bytes alone.
After 16 resizes the four figures held 50 textures (13.6 MB), one for every
width they had settled at. That is harmless at this size: it reaches the
256 MiB budget only after hundreds of resizes of large figures. Until then
it is memory nothing draws, except when the pane returns to a width it has
already been at, which is the 0-frame "revisit" row above.

The GUI process's RSS went from 66 MB to 295 MB over the run. That cannot be
pinned on textures: it includes llvmpipe, the font atlas, imscribe and numpy.

## Which costs are architectural and which are tunable

**Architectural** (they go away only with a different design):

- **The child round trip.** Render time, plus pickling RGBA through a pipe,
  plus collection at the next pump, which can add up to one frame. A
  separate process is required because resvg_py holds the GIL (R2).
- **The serial queue.** One child renders one request at a time, so N
  figures settle one after another (resize: 7 → 13 frames for 4 figures).
- **Mermaid's two hops.** Layout, then raster, which makes the extra
  placeholder jump.
- **The cold start**, about 340 ms here, paid by the first figure of a
  session.
- **That a debounce exists at all.** A render can take up to a second, so
  asking for one every frame of a drag is not an option.
- **A stretched texture in the meantime.** Without mipmaps, a shrink past
  3x is illegible while it lasts.

**Tunable** (constants or policy inside the current design):

- **`STABLE_FRAMES` (6).** It is counted in frames, not time: 100 ms at
  60 Hz, 50 ms at 120 Hz, 108 ms here. It is the largest single piece of
  resize latency for one figure.
- **Raster oversizing.** A raster is made at exactly the drawn width
  (`raster_size`), so any grow under the natural width magnifies, and any
  new width is a new raster. Rendering one size up (for example 1.25x, or
  power-of-two buckets, capped at natural width times density) would make
  small grows sharp at once. Most shrinks would then be served by a held
  raster minified under 2x, which T1 found legible. The price is about 1.6x
  the memory and render time per raster.
- **Queue order.** Today an svg raster waits behind mermaid layouts it does
  not need (75 ms against the 20 ms it costs). Rasters for on-screen
  figures could go first, or a ready raster could go ahead of a layout.
- **Pre-starting the child** at app start or at the first fence open would
  take the cold first paint from about 410–536 ms down to the warm 75–196 ms.
- **Evicting superseded widths** of the same content would bound the
  leftovers measured above.

## The bar

What a direct vector path (tessellating to the draw list each frame) would
have to match, and where it could win:

- **First outline: frame 0.** The current path already does this, so a
  vector path only matches it.
- **First image: 75–200 ms warm, 410–540 ms cold**, for three figures. A
  vector path drawn on the frame thread could show the image in frame 0. The
  cost moves to building the geometry, which has to stay inside a frame.
  Merman's layout still runs in the child (R2), so a mermaid figure still
  waits about 20–40 ms warm for it.
- **Resize to sharp: 7 frames for the first figure, 13 for four**, with
  stretched texture in between, at its worst minified 3.9x. A vector path is
  sharp in the frame the width changes. This is the cost where it would win
  most clearly.
- **Theme: free today.** A vector path that recolours ink by theme would add
  work that does not exist now.
- **Per-frame: 0.03 ms GUI and 0.02 ms render per figure, one quad each.**
  A vector path pays per vertex, every frame, for every figure on screen.
  A 1,200-label diagram is thousands of glyph quads and path triangles, so
  this is the number it is most likely to lose on, and it has to be measured
  on real diagrams before anything is decided.
- **Memory: w·h·4 per figure** (0.1–0.7 MB here at density 1, 4x at
  density 2), against a vector path's vertex buffers, which grow with the
  figure's complexity rather than its size on screen.

Cheaper changes inside the current design come before a new path:
pre-starting the child, putting on-screen rasters first in the queue,
oversizing rasters, and a `STABLE_FRAMES` counted in milliseconds. Together
they attack most of the measured latency without touching the frame thread.
None of them has been tried here.
