# Probe T1: a texture in a hosted block, its lifetime, its filtering, and minified legibility

Board task `fig-probe-t1`, 2026-10-20. It answers the T1 row of
`planning/2026-10-18-vector-displays-beside-typeset-text.md` §7. It gates §3.6
"Textures" and "Raster size". Nothing in `pptmstr/` was changed. The planning
record is not edited here; the lead integrates it.

**Basis.** Everything below is **run** unless it says otherwise. There was one
script, `scripts/figure_probe.py`, run on a private Xvfb through the repo's
wrapper:

    cd <worktree> && scripts/xvfb_shot.sh -- .venv/bin/python scripts/figure_probe.py

    imgui_bundle: "1.92.900"
    gl_renderer: "llvmpipe (LLVM 15.0.6, 256 bits)"
    gl_version: "4.5 (Core Profile) Mesa 22.3.6"
    exit=0, 4.0 s wall

The environment was this worktree's `.venv`: numpy 2.4.6, resvg_py 0.5.0
(resvg 0.48.1), PyOpenGL 3.1.10, imscribe 0.2.0 and imtypeset 0.4.3.192900.
The fonts and theme are pptmstr's `theme.load_fonts` and the `"dark"` theme.
The document theme and palette are `pptmstr.ui.typeset.themes`.

The full output is in `shots/figure-probe/results.json`, with the log beside
it in `shots/figure-probe-run.log`. The screenshots are in
`shots/figure-probe/` (gitignored), and the figures below quote them.

**What every number here has not been tried against:**
- a hardware GPU driver, because llvmpipe is a software rasteriser;
- the macOS backend;
- a DPI scale other than 1.

Bilinear filtering is specified by GL, so the legibility result should carry
over [inferred]. Upload times will not.

**The setup.** An `imscribe.TypesetView` is built with pptmstr's document
theme and a `host_for` that answers only a fence whose `language` is
`figure`. That is how `views._Fences` answers `_HOSTED_INFO`.

The document is a heading, a paragraph, the fence and five paragraphs, drawn
in a 900×470 bordered child window that scrolls. The view asked `host_for`
about 8 blocks and hosted block 2 (`asked_host_for`).

The host is a `Hosted` with these members:
- `text`;
- `height_for(w)`, from the texture's aspect;
- `draw()`, which calls `imgui.image` at the cursor.

Every create, read and drop of a texture runs in `pre_new_frame`, between
frames. The one exception is the hazard case in 3c.

---

## 1. An RGBA texture drawn by a host lands in the host's rectangle

**The `ImTextureRef` spelling.** `TextureGpu.texture_id` is a **method**
returning `int`, and `width` and `height` are attributes. Writing
`tex.texture_id` without the call raises
`TypeError: int() argument must be ... not 'nanobind.nb_bound_method'`. The
draw is:

    tex = hello_imgui.create_texture_gpu_from_rgba_data(rgba)   # HxWx4 uint8, C-contiguous
    imgui.image(imgui.ImTextureRef(tex.texture_id()), imgui.ImVec2(w, h))

The numpy-free path is different. `ImageAndSize.texture_id` is an
**attribute** (`int`), so that path uses `imgui.ImTextureRef(img.texture_id)`.

**Measured.** The texture is 600×300, magenta with a 1 px green border, and
it is drawn as the hosted block's widget. It sits between the prose paragraphs
(`q1.png`).

    q1: host_rect [62.0, 121.24, 600, 300]   (x is just under 62)
        magenta_px 178204 = 598 x 298 (the border is green)
        magenta_outside_host_rect 0, magenta_outside_child 0

The image's pixels are exactly the host's rectangle once its origin is
floored: green at column 61 and row 121, magenta from (62, 122).

**Conclusion.** The texture works and the rectangle holds. **Finding: the
host's origin is fractional.** The view hands the host y = 121.237. Drawn at
the unfloored cursor, the bilinear sample smears the 1× raster, as section 5
measures. **A figure host must floor (or round) its origin to whole
framebuffer pixels before `imgui.image`.**

## 2. Clipping

`TypesetView._draw_hosts` pushes no clip rect [read, `typeset_view.py`]. The
child is scrolled to its maximum, 154 px, so the host's top is 74 px above the
child's top edge (`q2a` to `q2c`).

| case | magenta px | outside host rect | outside child |
|---|---|---|---|
| q2a: partly scrolled out, image exactly the host's size, no clip of its own | 134 550 | **0** | **0** |
| q2b: same, image drawn 60 px taller than the rectangle, no clip | 169 832 | **34 684** | 0 |
| q2c: same overdraw, host does `push_clip_rect(rect, True)` | 135 148 | **0** | 0 |

The extra 598 px in q2c over q2a is one row: the stretched image's magenta
replaces the green bottom border row. It is not a leak.

**Conclusion.** The scrolled-out part needs nothing from the host. The
child's (or pane's) own window clip rect cuts it (q2a). The view's missing
clip rect matters only when a widget draws more than `height_for` claimed
(q2b, `q2b.png`: the image covers the prose under it). A host that pushes its
own rectangle with `intersect_with_current_clip_rect=True` contains that
(q2c). **Recommendation: the figure host pushes its rectangle as a clip rect
anyway.** It costs one draw command, and it makes a `height_for`/`draw`
disagreement, for example from a rounding difference, invisible instead of
overprinting text.

## 3. Lifetime and upload cost

**3a. Freed on the last reference.**

    q3_single: {"id": 3, "alive_while_held": true, "alive_after_drop": false}   # glIsTexture

`del` plus `gc.collect()` frees the GL name synchronously. With CPython
refcounting, `del` alone is enough when nothing else holds the handle
[inferred]. The `gc.collect()` is belt and braces.

**3b. No growth over create/drop cycles.** The texture is 2000×1500
(11.4 MB):

    60 create/drop cycles:  RSS 239.6 MB -> 239.6 MB   (no growth; 60 distinct ids, none alive after)
    control, hold 20 at once: RSS -> 457.1 MB  (+217.5 MB ~= 20 x 11.4 MB)
    release the 20:          held_ids_alive_after_release 0; RSS stays 457.1 MB

The control shows that on llvmpipe RSS does track texture memory, so the flat
60-cycle line is evidence of no leak. The RSS that stays up after the 20 are
released is the allocator keeping freed pages. One earlier run dropped back to
230 MB, and the later three did not. `glIsTexture` is the evidence of freeing,
not RSS.

**3c. Freeing between `imgui.image` and the frame's render is a defect, not a
hazard to tolerate.** The host drew the image and then dropped the last
reference in the same frame. The texture was deleted at once
(`alive_inside_frame_after_drop: false`), the render raised
**`GL_INVALID_OPERATION` (1282)**, and **nothing was drawn** (`q3_hazard`:
magenta 0). The content cache must therefore evict only outside the frame: in
`pre_new_frame`, or by parking an evicted `TextureGpu` for one frame before
dropping it. An LRU that evicts during `draw` because a new figure was
inserted there would hit this.

**3d. Upload time**, `create_texture_gpu_from_rgba_data` bracketed by
`glFinish`, 12 runs:

| size | median | min | max |
|---|---|---|---|
| 800×600 | 0.11 ms | 0.11 | 1.02 |
| 2000×1500 | 2.12 ms | 2.07 | 3.04 |

These are llvmpipe numbers, where an upload is a memcpy. A discrete GPU's
upload is a different cost and was not measured. At these sizes, an upload on
the GUI thread is not a frame-budget concern on this renderer.

## 4. Mipmaps and filters

Read with `glGetTexParameteriv` and `glGetTexLevelParameteriv` on both paths:

    TextureGpu (create_texture_gpu_from_rgba_data):
      min_filter LINEAR, mag_filter LINEAR, base_level 0, max_level 1000,
      level0 [600, 300], level1 [0, 0], level2 [0, 0]
    image_and_size_from_encoded_data:
      min_filter LINEAR, mag_filter LINEAR, ... level0 [640, 340], level1 [0, 0]

**There are no mipmaps, and minification is plain `GL_LINEAR`**: one 2×2
bilinear tap per output pixel, however large the reduction. That settles the
§3.6 [unverified] about mipmaps for the OpenGL backend. Other backends were
not checked. hello_imgui's source was not read; the GL state is the answer for
this build.

## 5. Legibility of a figure rasterised larger and minified by the GPU

**The figure.** A flowchart-like SVG at 640×340 user units: nine rounded boxes
and a diamond, 1 px strokes, 14 connectors with arrowheads, 11 px and 10 px
labels in `sans-serif`, and a 10 px caption. resvg rasterised it with
imscribe's prose and code faces as `font_files`, `skip_system_fonts=True`,
and every generic family mapped to "Roboto" (monospace to "Inconsolata
Medium"), per `svg-renderers.md`. The ground is white, so ink is measurable.

**The drawn rectangle** is 640×340 at a floored origin. Each raster is drawn
minified into it by the GPU, and the host's crop is read back. The 1× raster
is the reference: it is exactly what raster-at-drawn-width gives
(`mae_vs_1x_raster: 0.0` for the 1× capture, so the capture path is exact).

The integer ratios are compared with a CPU area-average (box filter) of the
same raster, which is what a mip chain approximates. The ratio 4.57 is a
2925 px raster shown at 640 px, §3.6's own example.

**The measures, all on luminance:**
- **ink kept** is the fraction of the 1×'s dark pixels (< 110) still < 110;
- **MAE** is the mean absolute difference from the 1× over the whole figure;
- **label-strip MAE** is the same over a strip of 10–11 px labels (rows
  262–300). The strip is mostly white ground, so it is the ratios between rows
  that matter, not the absolute values.

| raster | resvg ms | ink kept | MAE vs 1× | label-strip MAE | GPU vs box filter |
|---|---|---|---|---|---|
| 1× (640) | 23.8 | 1.000 | 0.00 | 0.00 | — |
| 2× (1280) | 56.5 | 0.982 | 0.32 | 0.51 | 0.01 (identical) |
| 2.5× (1600) | 82.4 | 0.996 | 0.51 | 0.91 | — |
| 3× (1920) | 109.2 | 0.998 | 0.88 | **1.62** | 0.90 |
| 4× (2560) | 176.4 | 0.998 | 0.69 | **1.30** | 0.66 |
| 4.57× (2925) | 229.8 | 0.998 | 0.85 | **1.59** | — |
| box filter of 4× (CPU) | — | 0.989 | — | 0.49 | — |
| 1× at the unfloored origin (y = 121.237) | — | **0.592** | **6.22** | — | — |

**What the numbers say:**
- **No 1 px stroke was lost at any ratio** (`ink_px_lost_to_white: 0`
  everywhere). The SVG's strokes are in user units, so a 1 px stroke at the
  drawn size is k px wide in the k× raster, and a LINEAR tap always lands on
  some of it. §3.6's specific fear ("enough to break 1 px strokes") **did not
  happen** for strokes that scale with the drawing. A non-scaling hairline
  (`vector-effect: non-scaling-stroke`, or a stroke under 1/k px) was not
  tested, and it is where strokes would break [inferred].
- **At exactly 2× the GPU equals the box filter** (difference 0.01). At an
  exact 2:1 ratio, a bilinear tap at the output pixel's centre falls between
  four texels and averages all of them.
- **At 3× and above, text aliases.** The label strip's error is 2.6–3.3 times
  the box filter's (1.30–1.62 against 0.49). A bilinear tap reads 2×2 of a
  k×k footprint, so most of each glyph's coverage is skipped and stem weight
  depends on phase.
- **Ink kept is not a good measure of this.** It rises above the 2× value
  (0.998), and "darkness retained" exceeds 1, because point-like sampling
  keeps stems at full darkness instead of averaging them with the ground. The
  aliasing is in stem weight and shape, which the eye reads; it is not lost
  ink.

**My visual read** of `shots/figure-probe/q5-labels-3x-zoom.png`. Its rows,
top to bottom, are 1×, 2×, 2.5×, 3×, 4×, 4.57× and the box filter of 4×,
each magnified 3× nearest-neighbour.
- 1× and the box filter are clean and nearly identical.
- 2× is indistinguishable from 1× at normal size.
- 2.5× is slightly uneven.
- **3×, 4× and 4.57× are visibly worse.**
  - Stems are uneven.
  - "rn" in "Warn" closes up toward "m" (at 4.57× it reads "Wam").
  - "L" in "(LRU)" breaks into "I.".
  - The "v" and "c" of "Evict" are jagged.

  They stay readable at 1:1, but they look like a broken font, not a soft one.

From the full crops (`q5_*x-crop.png`) at 1:1, the boxes, arrows and 11 px
labels hold at every ratio. The 10 px labels and the caption are where the
damage shows.

**The fractional origin is the larger effect.** The 1× raster drawn at the
cursor the view gives (y = 121.237) loses 41% of its dark ink to a sub-pixel
smear (MAE 6.22). That is about seven times worse than any minification above.

**Conclusion.** Raster-once-at-natural-size-and-let-the-GPU-downscale does
**not** measure acceptable at the ratios §3.6 worries about (3× and up). The
damage is to small text, not to strokes. Exactly 2× is safe, but only exactly
2×: the ratio is natural width over drawn width, which changes whenever the
pane is resized.

## 6. The numpy-free path, `image_and_size_from_encoded_data`

**What was run.** A resvg PNG (640×340) went through
`image_and_size_from_encoded_data` with keys `"fig-a"` (twice), `"fig-b"`,
and `""` (twice).

    ids: a1 123, a2 123 (same key -> same texture), b1 124, nokey1 125, nokey2 126
    alive before free_image_cache: a1 a2 b1 true; nokey1 nokey2 FALSE
    alive after  free_image_cache: all false
    "fig-a" asked again after the free -> new id 127 (re-decodes, works)
    a1 drawn in the host: MAE vs 1x raster 0.00 (q6.png)
    nokey1 drawn in the host: GL_INVALID_OPERATION (1282), nothing drawn (q6_nokey.png)

**Conclusions.**
- **It works with resvg's PNG**, and it draws pixel-exact.
- **An empty `cache_key` returns a dangling id.** The texture is already
  deleted when the call returns, and drawing it is a GL error. The binding's
  docstring ("if non-empty, the texture is cached") does not say that the
  uncached texture is not kept either. So this path is usable **only with a
  key**.
- **A single entry cannot be freed.** The API has `free_image_cache()`, which
  frees every keyed entry, and nothing per key. That cache is shared with
  everything else that loads images through hello_imgui. pptmstr itself
  calls none of `image_from_asset`, `image_and_size_*` or `free_image_cache`
  today [read, grep of `pptmstr/`]. Calling `glDeleteTextures` on an entry by
  hand would leave the cache holding a dead id under that key [inferred, not
  run].

---

## Recommendation on §3.6

**"Textures": the decision stands (numpy and `TextureGpu`, held by a bounded
content cache), with two additions.**
1. **Evict outside the frame.** Drop a `TextureGpu` only in `pre_new_frame`,
   or keep an evicted one for one more frame. Dropping it after `imgui.image`
   and before render is `GL_INVALID_OPERATION` and a blank figure (3c).
2. **The `TextureGpu` path needs a PNG decoder that is not in the venv.**
   `resvg_py` 0.5.0 has one entry point, `svg_to_bytes`, which returns **PNG**.
   There is no raw-RGBA output, and `create_texture_gpu_from_rgba_data` takes
   RGBA. resvg's PNGs use adaptive row filters, Paeth included (`Counter({2:
   146, 1: 35, 4: 19})` on a test render), so a numpy-only decoder needs a
   per-pixel Paeth loop in Python. The probe decoded through the encoded-data
   path and `glGetTexImage`, which is a probe's trick, not a design. The
   options are:
   - Pillow, which is a new dependency;
   - a pure-Python or numpy decoder, whose cost was not measured;
   - the encoded-data path with a key, accepting all-or-nothing freeing.

   §3.6 lists numpy as the cost of this path; **it is numpy plus a decoder.**
   This is the one finding that reopens a slice-1 dependency question.

The encoded-data path is not a fallback worth keeping. It needs a key to be
usable at all (6), and its eviction is the whole cache.

**"Raster size": raster at the drawn width × DPI scale stands, and the
simpler raster-once-and-downscale is not reinstated.** GPU minification has
no mipmaps (4), and at 3× and above it degrades 10 px labels visibly (5).
Three refinements:
- **Snap the host's origin to whole pixels.** It is worth more than any raster
  choice: an unsnapped 1× raster loses 41% of its ink to the sub-pixel smear.
- **While a re-raster is pending after a resize, showing the previous texture
  minified is acceptable as a transient up to about 2.5×.** Beyond that it
  reads as a broken font. The previous texture is the stale raster
  §3.6's "re-rasterise when the drawn width has been stable for a few frames"
  implies.
- **Mipmaps are an unmeasured alternative.** They would be `glGenerateMipmap`
  plus `GL_LINEAR_MIPMAP_LINEAR` set through PyOpenGL on the id. PyOpenGL is a
  `dev` extra today, not a runtime dependency, and this does not reach a
  non-GL backend, so it is not recommended without its own probe.
