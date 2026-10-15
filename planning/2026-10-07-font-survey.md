# No installed open font earns a place in the wheel ahead of the Roboto that already ships

**Dated:** 2026-10-07 ·
**Status:** survey run; nothing vendored, no font binary added; the recommendation is a
question for the operator (§8) ·
**Origin:** the operator, 2026-10-07 — *"I would be more keen to ship with fonts that are
bundled already but even checking installed in order to find an open font we could bundle
ourselves may be worthwhile."* ·
**Engages with:** `planning/archive/2026-08-10-transcript-markdown.md` — *"Emphasis is a
colour shift, not a slant"*, and its rejection of *"Vendoring a 4-face family"* ·
**Measured with:** `scripts/survey_fonts.py`, fontTools 4.66.0, imgui-bundle 1.92.900,
Debian 12 font packages at the versions in §2 ·
**Looked at:** 17 PNGs under `shots/`, rasterised by FreeType 2.13.3 and drawn by
llvmpipe on Xvfb — not a GPU, not a physical display (§5)

---

## 1. What ships today, and under what terms

Evidence is the font's own `name` table (records 0, 13, 14) read with fontTools, and a
licence file where one sits beside the font. No Debian package owns any of these files.

| font | where | licence, as the file states it | licence text shipped beside it |
|---|---|---|---|
| Roboto Regular / Bold / RegularItalic / BoldItalic (2.137) | imgui-bundle `assets/fonts/Roboto/` | name 13: *"Licensed under the Apache License, Version 2.0"* | yes — `Roboto/LICENSE.txt`, Apache 2.0 |
| Inconsolata-Medium (3.001) | imgui-bundle `assets/fonts/` | name 13: SIL Open Font License 1.1 | **no** |
| Inconsolata-Bold (3.100) | `pptmstr/assets/fonts/` | name 13: SIL Open Font License 1.1 | yes — `pptmstr/assets/fonts/OFL.txt` |
| DroidSans (1.00) | imgui-bundle `assets/fonts/` | name 13: Apache License 2.0 | **no** |
| Latin Modern Math (1.959) | imgui-bundle `assets/fonts/latex/` | name 0: *"released under the GUST Font License"*; names 13, 14 absent | **no** |
| FontAwesome 4.7.0 | imgui-bundle `assets/fonts/` | name 14: `http://fontawesome.io/license/`; name 13 absent | **no** |
| Font Awesome 6 Free Solid (6.5.1) | imgui-bundle `assets/fonts/` | name 0: *"Copyright (c) Font Awesome"*; names 13, 14 absent | **no** |

Neither Inconsolata file names a Reserved Font Name. The four "no" rows are imgui-bundle's
wheel, not pptmstr's; they are recorded because pptmstr depends on that wheel, not because
anything here can fix them. Whether the wheel's top-level `LICENSE` covers them was not
read.

## 2. What was surveyed

fontconfig lists 554 families here (the 2,416 in the task is `fc-list`'s line count, one
per file and style). `scripts/survey_fonts.py` keeps a family that has an upright regular
and an upright bold at normal width in a `.ttf`/`.otf` — 161 — and whose regular face maps
at least 200 Latin letters — 39. Of those, 16 are measured; the other 23 are named in the
script's `SET_ASIDE` with the reason (Arabic, Uyghur and Thai families carrying a companion
Latin; display cuts; clones sharing a package and licence with a measured family). A family
that passes on another machine and is in neither list prints as `UNCLASSIFIED`.

Added to the lead's shortlist: Andika, FreeMono, FreeSans, Nimbus Mono PS, Nimbus Sans,
Yrsa. Confirmed absent: Inter, IBM Plex, JetBrains Mono, Source*.

Licence evidence, both sources, for the regular face of each:

| family | package (version) | `Files: *` `License:` in `/usr/share/doc/<pkg>/copyright` | font `name` 13 | condition that bears on redistribution |
|---|---|---|---|---|
| Hack 3.003 | fonts-hack 3.003-3 | `Expat and BitstreamVera` | MIT for the Hack work; Bitstream Vera for the base | notices travel with every copy; a modified font may not be named "Bitstream" or "Vera"; may not be sold by itself |
| DejaVu Sans Mono / Sans / Serif 2.37 | fonts-dejavu-core 2.37-6 | `bitstream-vera`, *"DejaVu changes are in public domain"* | same, in full | as Hack's Vera half. DejaVu Sans's name 0 adds a 2006 Tavmjong Bah (Arev) copyright; the mono's does not |
| Liberation Mono / Sans / Serif 2.1.5 | fonts-liberation2 2.1.5-1 | `SIL-OFL-1.1` | OFL 1.1 | *"with Reserved Font Name Liberation"* |
| Noto Sans / Serif 2.004, Noto Sans Mono 2.006 | fonts-noto-core, fonts-noto-mono 20201225-1 | `OFL-1.1` | OFL 1.1 | none named in either source |
| Andika 6.101 | fonts-sil-andika 6.101-2 | `OFL-1.1-RFN` | OFL 1.1 | Reserved Font Names "Andika" and "SIL" |
| Yrsa 2.004 | fonts-yrsa-rasa 2.005-1 | `sil-1.1` | OFL 1.1 | none named |
| FreeMono / FreeSans 0412.2268 | fonts-freefont-ttf 20120503-10 | `GPL-3+ with Special Font Exception` (the fonts' own stanza) | GPL, as free software | GPL-3+; the exception covers a *document* embedding the font, and whether an application wheel is one is not something this record can settle |
| Nimbus Mono PS / Sans 1.00 | fonts-urw-base35 20200910-7 | `AGPL-3 with Font exception` | **absent** | as FreeFont, with AGPL in place of GPL |

Under the OFL a "Modified Version" is *"any derivative made by adding to, deleting, or
substituting — in part or in whole — any of the components"* (the text in
`/usr/share/doc/fonts-liberation2/copyright`), and a modified version may not use a
Reserved Font Name. So **subsetting a Liberation or Andika file to save bytes would oblige
a rename**; subsetting a Noto, Yrsa, DejaVu or Hack file would not, on the terms quoted
here. Upstream licence pages were not fetched; every statement above is from a file on this
machine.

## 3. The table

At a 16 px ImGui pixel height. All numbers are fontTools reading the regular face directly,
not `advtext`; `advtext.em_size_for_pixel_height` was called on the two bundled faces as a
cross-check and agrees to the third decimal (Roboto 13.653, Inconsolata 15.253). `adv` is
the mean unkerned advance over a fixed 302-character paragraph (the monospace cell, for a
monospace); `ch/370` is 370 divided by it; `lines` is that paragraph greedily wrapped at
370 px. x-height and cap-height are the outline tops of `x` and `H`. Sizes are all the
faces a family has, in kB.

| family | mono | em px | adv px | x-h px | cap px | ch/370 | lines | faces | italic | kB |
|---|---|---|---|---|---|---|---|---|---|---|
| **Roboto** (ships) | no | 13.65 | 5.97 | 7.21 | 9.71 | 62.0 | 6 | 4 | Italic | 662 |
| **Inconsolata** (ships) | yes | 15.25 | 7.63 | 7.02 | 9.50 | 48.5 | 7 | 2 | none | 203 |
| Hack | yes | 13.74 | 8.28 | 7.52 | 10.02 | 44.7 | 8 | 4 | Italic | 1220 |
| DejaVu Sans Mono | yes | 13.74 | 8.28 | 7.52 | 10.02 | 44.7 | 8 | 4 | Oblique | 1158 |
| Liberation Mono | yes | 14.12 | 8.48 | 7.46 | 9.30 | 43.7 | 8 | 4 | Italic | 1165 |
| Noto Sans Mono | yes | 11.75 | 7.05 | 6.30 | 8.39 | 52.5 | 6 | 2 | none | 1010 |
| FreeMono | yes | 16.00 | 9.60 | 6.67 | 9.01 | 38.5 | 9 | 4 | Oblique | 939 |
| Nimbus Mono PS | yes | 16.00 | 9.60 | 6.70 | 9.01 | 38.5 | 9 | 4 | Italic | 330 |
| Noto Sans | no | 11.75 | 5.50 | 6.30 | 8.39 | 67.2 | 5 | 4 | Italic | 2044 |
| DejaVu Sans | no | 13.74 | 6.90 | 7.52 | 10.02 | 53.6 | 6 | 4 | Oblique | 2687 |
| Liberation Sans | no | 14.32 | 6.29 | 7.57 | 9.85 | 58.8 | 6 | 4 | Italic | 1611 |
| Andika | no | 9.93 | 4.58 | 4.95 | 7.08 | 80.8 | 4 | 4 | Italic | 2696 |
| FreeSans | no | 14.55 | 6.20 | 7.62 | 10.60 | 59.7 | 6 | 4 | Oblique | 1737 |
| Nimbus Sans | no | 16.00 | 7.03 | 8.38 | 11.66 | 52.7 | 7 | 4 | Italic | 348 |
| Noto Serif | no | 11.75 | 5.56 | 6.30 | 8.39 | 66.6 | 5 | 4 | Italic | 2436 |
| DejaVu Serif | no | 13.74 | 6.98 | 7.13 | 10.02 | 53.0 | 7 | 4 | Italic | 1398 |
| Liberation Serif | no | 14.45 | 5.72 | 6.63 | 9.46 | 64.7 | 5 | 4 | Italic | 1481 |
| Yrsa | no | 16.00 | 6.26 | 6.66 | 9.10 | 59.1 | 6 | 4 | Italic | 668 |

"Italic" and "Oblique" are the style name the font gives itself, not a judgement.

Coverage of the regular face's `cmap` — → ← ⇒ / ✓ ✗ / … — / box-drawing U+2500–257F /
Greek both cases / ≤ ≥ ≠:

| family | arrows | ticks | … — | box | Greek | ≤ ≥ ≠ | lacks |
|---|---|---|---|---|---|---|---|
| **Roboto** | 0/3 | 0/2 | 2/2 | 0/128 | 49/49 | 3/3 | → ← ⇒ ✓ ✗, all box-drawing |
| **Inconsolata** | 2/3 | 2/2 | 2/2 | 128/128 | 0/49 | 3/3 | ⇒, all Greek |
| Hack | 3/3 | 0/2 | 2/2 | 128/128 | 49/49 | 3/3 | ✓ ✗ |
| DejaVu Sans Mono | 3/3 | 2/2 | 2/2 | 128/128 | 49/49 | 3/3 | — |
| Liberation Mono | 2/3 | 0/2 | 2/2 | 40/128 | 49/49 | 3/3 | ⇒ ✓ ✗ |
| Noto Sans Mono | 3/3 | 0/2 | 2/2 | 128/128 | 49/49 | 3/3 | ✓ ✗ |
| FreeMono | 3/3 | 0/2 | 2/2 | 128/128 | 49/49 | 3/3 | ✓ ✗ |
| Nimbus Mono PS | 3/3 | 0/2 | 2/2 | 44/128 | 49/49 | 3/3 | ✓ ✗ |
| Noto Sans, Noto Serif | 0/3 | 0/2 | 2/2 | 0/128 | 49/49 | 0/3 | → ← ⇒ ✓ ✗ ≤ ≥ ≠, box |
| DejaVu Sans | 3/3 | 2/2 | 2/2 | 128/128 | 49/49 | 3/3 | — |
| Liberation Sans, Serif | 2/3 | 0/2 | 2/2 | 40/128 | 49/49 | 3/3 | ⇒ ✓ ✗ |
| Andika | 3/3 | 1/2 | 2/2 | 0/128 | 20/49 | 3/3 | ✗, box, most Greek |
| FreeSans | 3/3 | 0/2 | 2/2 | 105/128 | 49/49 | 3/3 | ✓ ✗ |
| Nimbus Sans | 3/3 | 0/2 | 2/2 | 44/128 | 49/49 | 3/3 | ✓ ✗ |
| DejaVu Serif | 3/3 | 0/2 | 2/2 | 128/128 | 49/49 | 3/3 | ✓ ✗ |
| Yrsa | 2/3 | 0/2 | 2/2 | 0/128 | 4/49 | 3/3 | ⇒ ✓ ✗, box, Greek |

## 4. Two premises the measurement corrected

**Inconsolata lacks much less than the task assumed.** Of the list it was said to lack —
arrows, ticks, `… —`, box-drawing, Greek, `≤ ≥ ≠` — the bundled Inconsolata-Medium maps
everything except `⇒` and the Greek alphabet. Coverage is therefore a weak argument for
replacing it: **Roboto and Inconsolata between them miss exactly one tested character,
`⇒`**, if a glyph absent from one can be drawn from the other. Nothing in this survey shows
that it can (§5).

**Hack is not the only monospace with a real italic, and not the best-covered.** DejaVu
Sans Mono has the same four faces and, at 16 px, identical em, cell, x-height and
cap-height to Hack — Hack's own copyright file says it derives from Bitstream Vera Sans
Mono. DejaVu's slanted faces are named "Oblique" where Hack's are named "Italic"; in the
renders the two were not distinguishable to this reader at this size. DejaVu Sans Mono maps
`✓ ✗`, which Hack does not, and is 62 kB smaller.

## 5. What was looked at

`XVFB_SHOT_SCREEN=1600x1300x24 scripts/xvfb_shot.sh -- .venv/bin/python scripts/survey_fonts.py --render`
wrote `shots/fonts-<family>.png` for Roboto and the 16 candidates, each beside a
`-zoom.png` (the top 330 rows, 3× nearest-neighbour). One fixed markdown document — two
headings, a paragraph with bold, italic, bold-italic and inline code, a list, a python
fence, a table, a line of symbols — in two 370 px panes: the candidate on the left, and on
the right Inconsolata in every role through `advtext`'s all-mono theme. That right pane is
*not* pptmstr's own renderer. A proportional candidate takes the four prose roles and
leaves code to Inconsolata; a monospace candidate takes every role.

Each run printed `loader=FreeType (2.13.3; 2.13.3)  renderer=llvmpipe (LLVM 15.0.6, 256
bits)`. So the raster judged is **FreeType's, with whatever hinting flags ImGui passes it,
drawn by a software rasteriser** — no GPU, no LCD subpixel rendering, 1× scale. Every 1×
PNG was opened; the zooms opened were Hack, DejaVu Sans Mono, Roboto and DejaVu Sans, and
the viewer scaled those to 87%, so they are not pixel-exact.

What was seen:

- **Roboto** (`fonts-roboto.png`): the cleanest prose here, a true italic clearly distinct
  from the upright, the whole document in 435 px of height against 550 px for the
  all-Inconsolata pane. Inline code beside it reads visibly *smaller* than the prose: code
  is set at the body's em, and Inconsolata's 16 px is a larger em than Roboto's.
- **Hack, DejaVu Sans Mono**: crisp, even stems, legible italic; 531 px. Wider than
  Inconsolata — the paragraph breaks earlier on every line.
- **Liberation Mono**: wider still and loosely spaced. **FreeMono, Nimbus Mono PS**:
  Courier; thin strokes on the dark ground, the second heading wraps, 668 px. Not for this
  UI.
- **Noto Sans Mono**: the only monospace denser than Inconsolata (407 px), because its 16
  px is an 11.75 px em — the text is simply small. No italic.
- **Noto Sans, Noto Serif**: very dense (375 px) for the same reason, and at that size the
  inline code is smaller again. **Andika**: 291 px and too small to read comfortably; its
  line spacing is what the pixel height is being spent on.
- **DejaVu Sans**: wide and sturdy, the one prose face with every tested symbol on screen;
  475 px. **Liberation Sans, FreeSans, Nimbus Sans**: Helvetica-like, tight letterfit at
  this size, FreeSans visibly cramped.
- **The serifs**: legible, DejaVu Serif best of them; none looks like a developer tool.

Not concluded from the pictures: anything about hinting on a real display, and anything
about Noto Serif and Yrsa in true bold — those two and Noto Sans were first rendered with a
demibold, the script was corrected, all three were re-rendered, and only Noto Sans was
opened again.

Two things in the pictures are unexplained and are recorded rather than smoothed over:

- **A character the `cmap` lacks is sometimes drawn anyway.** `≤ ≥ ≠` appear in the Noto
  Sans pane, and `✗` in several panes whose face does not map it, while `✓` in the same
  panes is the `�` replacement. Something on the atlas path substitutes from another font
  for some codepoints. The coverage table is the `cmap`; the pictures under-report gaps.
  `advtext`'s `DrawStats.missing` was 0 in every run and does not count these.
- **Word gaps in the all-Inconsolata pane are uneven** ("emphasised phrase" is visibly
  tighter than "phrase long" on the same line). It is the same in all 17 PNGs, so it is not
  a property of any candidate; whether it is `advtext`'s spacing or pixel snapping was not
  investigated.

## 6. Recommendation: bundle nothing; set prose in the Roboto that already ships

**Cost: zero bytes.** Roboto's four faces arrive with imgui-bundle, which pptmstr already
requires, under Apache 2.0 with the licence text beside them, and `advtext` already maps
its prose roles to them.

The reasons, against the alternatives:

- **No installed prose face beats it on this evidence.** The only ones denser at 16 px are
  denser because they are smaller (Noto, Andika) or are serifs. Liberation Sans is the
  nearest sans at 58.8 characters per line to Roboto's 62.0, costs 1.6 MB, and carries a
  Reserved Font Name.
- **The monospace-with-italic is real, and it is DejaVu Sans Mono rather than Hack** (§4) —
  but it is not worth its price today, for three reasons that each stand alone:
  1. *Density.* Its cell is 8.28 px to Inconsolata's 7.63 px: 44.7 characters across the
     DETAIL dock instead of 48.5, and the sample paragraph in 8 lines instead of 7. To
     match Inconsolata's columns it would have to be drawn at about 14.7 px.
  2. *Size.* 1,158 kB for four faces. The tracked files under `pptmstr/` total 1,238 kB, so
     it would roughly double the package — the same order as the ~1.4 MB the August record
     already declined to spend on JetBrains Mono.
  3. *`advtext` has nowhere to put it.* `Face` has `CODE` and `CODE_BOLD` and no monospace
     italic; `Faces.mono()` sets italic upright. The survey reached Hack's italic only by
     registering it under the *prose* roles and declaring the face table proportional, and
     a proportional table does not keep columns. A mono italic in pptmstr's all-mono mode
     needs a role added to `advtext` first, which is outside this tree.

**What this lets pptmstr revisit:** *"Emphasis is a colour shift, not a slant"*
(`planning/archive/2026-08-10-transcript-markdown.md`). That decision was forced by
Inconsolata having no italic and by the cost of vendoring a family. For any pane set in
prose through `advtext`, neither holds: the italic is a real face and it is already paid
for. For a pane that stays monospace the decision stands, and for the reason it was made.

**If the operator wants a slant in monospace panes anyway**, the candidate is DejaVu Sans
Mono, four faces, 1,158 kB, Bitstream Vera terms: ship its notice beside the files as
`OFL.txt` is shipped today, and do not sell the fonts alone. It is the only installed
monospace that maps every character tested.

## 7. What nobody verified

- How any of these looks on a GPU, on an LCD, or above 1× scale.
- That ImGui's FreeType loader sizes a face by `hhea` ascender minus descender. The
  arithmetic here is `advtext.fontlib`'s and agrees with it; the loader's source was not
  read, and no pixel in a PNG was measured against the table.
- That 370 px is the DETAIL dock's width. It is the task's figure; `app.py`'s split ratios
  on a 1500 px window give about 379 px, and a pane's text measure is narrower than its
  dock by its padding.
- Kerning. Advances are unkerned, so characters-per-line slightly understates Roboto and
  the other proportional faces.
- The fallback path in §5, and therefore whether Roboto's missing arrows, ticks and
  box-drawing can be drawn from Inconsolata without a seam change.
- Any upstream licence page. The licence facts are the files' and Debian's.
- Whether imgui-bundle's wheel-level `LICENSE` covers the five fonts it ships without a
  licence file beside them (§1).
- The 23 set-aside families were not measured or rendered.

## 8. Open, for the operator

1. **Is prose in Roboto acceptable for the panes `advtext` sets, with monospace kept for
   code, diffs and literal output?** That is the recommendation, and it is the only option
   here that adds an italic at no cost. Yes reopens the August emphasis decision for those
   panes; no leaves it standing everywhere.
2. **Is a slant in monospace panes worth about 1.2 MB and 8% of the columns?** If so the
   font is DejaVu Sans Mono, and the first step is a monospace-italic role in `advtext`,
   not a vendored file.
3. **Should the code face be sized to match the prose beside it?** At the same em,
   Inconsolata reads smaller than Roboto in every prose render. That is `advtext`'s
   recorded "code is set at the body size" decision meeting a face pairing, and it is a
   seam question rather than a font one.
