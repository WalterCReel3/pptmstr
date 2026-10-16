# ImGui text and the pixel grid

**Dated:** 2026-10-16 · **Status:** measurement; the recommendation at the end is the
builder's and nothing here is decided · **Probe:** `scripts/font_raster_probe.py`,
tests in `tests/test_font_raster_probe.py` · **Board task:** `font-raster` (spec in
`font-raster-probe`)

**This is not a planning record.** It answers the operator's question on the tab-text
sheet: "Are imgui tabs just rendered in an awkward way? None of these outside of 15px
look like good pixel-grid alignment." If anything below is adopted, the decision
belongs in `planning/`.

## Answer

The operator's eye was right that only 15 px is crisp. The cause is not misalignment
to the grid, and it is not specific to tabs.

- **Every glyph quad lands on a whole pixel** at 15, 16, 17 and 18 px. This holds in a
  tab, a menu item and a button, read from the draw list's vertices.
- **What moves is where the vertical stem falls inside the glyph's pixels.** FreeType
  rasterises the glyph under the font's hinting, and at 16 and 17 px that leaves a
  roughly 1.3 px stem straddling two pixel columns, so neither column is solid.
- **Tab, menu and button text measure identically** at each size. So does the same
  word set by the text library through pptmstr's seam. Every piece of ImGui-drawn text
  in the app has this; tabs are only where it was noticed.

## Facts, read from the live atlas

These were read in the running app (`--fake`, dark and light) by `facts()`.

- **Loader:** `io.fonts.font_loader_name` is `FreeType (2.13.3; 2.13.3)`, not
  stb_truetype. A bare context reports the same, so it is the binding's default
  loader rather than something hello_imgui picks.
- **Atlas:** `font_loader_flags` is `0x0`, `renderer_has_textures` is True, and
  `font_scale_dpi` and `font_scale_main` are both 1.
- **Where pptmstr loads its fonts:** `theme.load_fonts`, from hello_imgui's
  `load_additional_fonts`, calls `hello_imgui.load_font_ttf_with_font_awesome_icons(
  "fonts/Inconsolata-Medium.ttf", 16)` and then `load_font_ttf` for the vendored bold.
- **The UI font as loaded:** the Inconsolata-Medium and Inconsolata-Bold sources are
  both at ImFontConfig defaults: `pixel_snap_h` False, `oversample_h` 0 and
  `oversample_v` 0, `font_loader_flags` 0x0, `rasterizer_density` 1. Under ImGui's
  FreeType loader, flags 0 means `FT_LOAD_TARGET_NORMAL`, which is normal hinting.
- **Hinted advances are whole pixels:** 7, 8, 8 and 9 px at 15, 16, 17 and 18 px.
  With `NoHinting` they are 7.156, 7.625, 8.109 and 8.578, and only then do the quads
  go off the grid (by up to 0.5 px).
- **There is no oversampling:** the atlas has 1.00 texel per screen pixel in every
  configuration. Upstream's `imgui_freetype.cpp` carries "FIXME: cfg.OversampleH,
  OversampleV are not supported". That was read from master and v1.92.0, not from the
  1.92.9 source this wheel was built from.
- **The text library loads its own copy of Inconsolata** (source 7, `oversample_h` 1,
  flags 0). It is drawn by the same FreeType loader.

So the lead's starting reading, from the stubs and not measured, does not hold. It
said `oversample_h` auto, glyphs at fractional x, and the 0.5 em advance putting
alternate glyphs on half pixels. Those mechanisms belong to stb_truetype, and this
build does not use it.

## How each configuration was loaded

ImGui 1.92 bakes a font per size on demand, under that font's own `ImFontConfig`, so
nothing was reloaded. The probe wraps `theme.load_fonts` in its own process only.
After everything pptmstr loads, it loads the same file once per configuration through
the same call pptmstr uses, `hello_imgui.load_font_ttf(asset, 16, cfg)`, and each
configuration becomes its own `ImFont` on the atlas. `Fonts[0]` is never touched, and
configuration (a) is the live font itself.

The control (a0) is the file loaded by the probe with a default config. It measures
identical to (a) on every label at every size, so the probe's loading adds nothing of
its own.

## Method

Each cell pushes one whole-pixel size with `push_font` and draws three widgets in a
child window of its own:

- a real `begin_tab_bar`/`begin_tab_item("session")`;
- `menu_item("Settings")`;
- `button("Dismiss")`.

Row (f) sets the same three words through the seam: `gate.literal_view(
themes.document_theme())` with `themes.literal_style(LITERAL)`, re-sized with
`themes._sized` to the cell's pixel height in the CODE face.

The probe reads two things back:

- **Vertices:** each label's glyph quads, which are the vertices whose uv is not the
  atlas's white pixel. From them it takes the farthest left or top edge from a whole
  pixel, and the texels per pixel of the `i` quad.
- **The frame:** the `i` stem. The `i`'s body is the lowest run of inked rows. The stem
  rows are the body rows where the ink is narrowest, which leaves out the arm and the
  foot. Across those rows the probe reads the solid share and the mean ink per column.
  The ink is the quad's vertex colour, and the ground is the crop's commonest colour.

The column profile is the reliable reading. The solid share counts a 0.16 fringe as
inked (`INK_LO` 0.15), so a one-column stem reads "50%". The `edge` share is the
tab-text sheets' measure, kept for comparison.

## The table

The `i` stem's column profile is in the `session` tab, on dark. Light agrees to within
0.01 to 0.02 in every column, with the same solid columns.

| config | 15 px | 16 px | 17 px | 18 px |
|---|---|---|---|---|
| (a) today | .05 **1.00** .16 | .83 .46 | .64 .76 | .41 **1.00** .05 |
| (a0) control | same as (a) | same | same | same |
| (b) `pixel_snap_h` True | same as (a) | same | same | same |
| (c) `oversample_h` 1 | same as (a) | same | same | same |
| (d) both | same as (a) | same | same | same |
| (e0) NoHinting | .08 .42 .65 .15 · off grid 0.47 | .41 .67 .28 · off grid 0.50 | .34 .72 .37 · off grid 0.45 | .26 .80 .38 · off grid 0.47 |
| (e1) LightHinting | **.99** .25 | .79 .53 | .58 .82 | .38 **1.00** .10 |
| (e2) ForceAutoHint | .23 **1.00** .04 | .28 **1.00** .09 | .32 **1.00** .13 | **1.00** .53 |
| (e3) Monochrome | **1.00** (no AA) | **1.00** | **1.00 1.00** | **1.00** |
| (e4) Monochrome + MonoHinting | same as (e3) | same | same | same |
| (f) imtypeset via the seam | same as (a) | same as (a) ±.01 | same | same |

What the rows show:

- **(a) to (d) are identical:** "same" means the same profile, quads and edge counts,
  diffed line by line. Every row but (e0) has its quads on whole pixels at every size.
- **(e2) changes the shape:** the `i`'s bitmap moves right a pixel at 17 and 18 px
  (`x0` 1 to 2), and the glyph is narrower at 15 and 16 px.
- **Today's edge shares** on the tab label: 58% at 15 px, 69% at 16, 67% at 17 and 59%
  at 18. Under (e2) they are 61, 59, 61 and 63%, and under (e3) 0% at every size.

**Which configuration puts the text on the grid at each size?**

- (a) to (d), which is today: 15 and 18 px only.
- (e2) ForceAutoHint: every size has a solid column. At 15 to 17 px the stem is one
  column with a fringe of 0.23 to 0.32; at 18 px it is 1.5 px wide.
- (e3) and (e4) Monochrome: every size, 100% solid, at the cost of anti-aliasing on
  every curve and diagonal. The 4x strip shows the jagged `s`.
- (e1) LightHinting: the same sizes as today. It snaps vertically only, by design.

## What it costs

- **`pixel_snap_h` and `oversample_h`** cost nothing and buy nothing under this loader,
  as measured above. The stub's warning, "prevents fractional font size from working
  correctly", concerns fractional sizes, and pptmstr binds whole pixels only
  (`gate.pushed`). Fractional sizes were not measured.
- **ForceAutoHint (0x4)** replaces Inconsolata's own hinting with FreeType's
  auto-hinter. Every Latin glyph's shape and position inside its cell changes (the `i`
  moves at 17 and 18 px), and so does the text's apparent weight. The advances do not
  change, so columns and widths hold.
- **Monochrome (0x80)** drops anti-aliasing. At these sizes that is a different look,
  not a sharper version of this one.

## Why the stems straddle: what is and is not established

Established, by measurement:

- Under the default load, the font's hinting rounds the advances to whole pixels.
- It does not put the vertical stem edges on pixel boundaries at 16 or 17 px.
- The auto-hinter does put them there.

Not established: why the native hinting leaves x alone. The likely cause is FreeType's
v40 TrueType interpreter, which ignores x-direction instructions, or the font's hints
being y-only. As a check, one run set `FREETYPE_PROPERTIES=truetype:interpreter-version=35`.
Today's row measured byte-identical, but nothing showed whether the variable was
honoured, so that run decides nothing.

## Recommendation (the builder's)

1. **Do not single out tabs.** Tab text is ImGui text like the rest. A tab-only font or
   flag would make the tabs the one place the app's monospace is drawn differently, so
   any change should be app-wide.
2. **The candidate is ForceAutoHint** on the UI fonts' `ImFontConfig.font_loader_flags`
   in `theme._load_face`, for both the body and the bold face.
   - It is the only anti-aliased configuration with a solid stem column at every size
     from 15 to 18 px.
   - Its cost is a changed glyph shape, so the operator should judge it against
     today's look on the 4x strip, not on these numbers.
   - The panes' typeset text uses the library's own Inconsolata source, which this
     flag does not reach. To keep them matching, either set the atlas-wide
     `io.fonts.font_loader_flags`, which upstream documents as shared by all fonts, or
     have the library take the flag. The atlas-wide path was not measured.
3. **If the shape change is unwanted,** the measured way to crisp tab text with no
   rasteriser change is a size whose stem lands whole: 15 px (or 18 px). This is
   against the 17 px tab-text-variants recommended: at 17 px the `i` stem is never
   solid.

## Images

`shots/style-preview/font-raster/{dark,light}.png` hold every cell with its numbers,
and a 4x nearest-neighbour strip of the `session` tab label for every configuration
at each size. Each `-raw.png` beside them is the frame the measurements were read from.

Render them with `.venv/bin/python scripts/font_raster_probe.py`, which runs one Xvfb
per theme. `--theme NAME` renders one theme on the display there is, and `--facts`
prints the bare-context loader.
