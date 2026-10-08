# Four face sets from the fonts already in the wheel, switched without a restart

**Dated:** 2026-10-07 (built 2026-10-08) ·
**Status:** built on `adv-text-experiment`; nothing vendored, no font binary added.
**The presets of §1 and §2 are superseded by per-role choices (§0, 2026-10-08)**;
§3's measurements and §4's pictures are of the presets and stand as what was seen ·
**Origin:** the operator, 2026-10-07 — *"I would be more keen to ship with fonts that are
bundled already but even checking installed in order to find an open font we could bundle
ourselves may be worthwhile."* ·
**Read with:** `planning/2026-10-07-font-survey.md`, which is the second half of that
sentence: what ships and its licences (§1), the installed-font table (§3), and the
recommendation to bundle nothing (§6). None of it is repeated here. ·
**Measured with:** imgui-bundle 1.92.900 under `scripts/xvfb_shot.sh` (Xvfb, llvmpipe,
FreeType), throwaway drivers under the gitignored `shots/fonts/` ·
**Looked at:** 11 PNGs under `shots/fonts/`, each named in §4

---

## 0. Superseded, 2026-10-08: text is configured by role, not by named preset

**The operator's words.** On choosing faces: *"Per pane setting for the font
selections is what's driving me that direction. Another option is to set the font
per _role_. We would need to catalog the output types."* and, to the lead's per-role
recommendation, *"yeah. Let's do that."* Asked whether `droid` should stay, the
answer was *"Preset?"* — presets were never the operator's concept; they were this
record's. On line spacing: *"1.35 was an arbitrary default that truly wasn't decided
on. I like the idea of per-role but let's hide that setting internally and just make
it easy to tweak via code for now. I don't want settings to get too fiddly."*

**What replaces §1.** `ui/typeset.py` holds the catalogue, `Role`: prose, headings,
inline code, code blocks, diffs, tool output (literal text), arguments, tables, row
titles (native labels). For each the operator may choose a `RoleStyle`: a `Slot`
(proportional or monospace) and a size step in whole pixels relative to the body.
`Settings.typeset_roles` holds only the roles set apart from `DEFAULTS`,
`Settings.typeset_family` the family in the proportional slot, and
`Settings.typeset_panes` which panes typeset at all (all five by default, under
`Settings.typeset`). The Text menu offers all of it; a settings dialog is to replace
the menu.

**Why a slot and not a family per role: checked against the library, and it holds.**
advtext's `Face` has seven members (four proportional, two monospace, one for
mathematics) and `register_font` keeps one file per member for the process, clearing
every metric cache when it changes. So two text families are live at once. What is
*not* limited is which of the two a role uses: a `Theme` gives each block kind its
own `FontSpec`, and `Theme.face_for` reads a block's family off the block's own
face, so a monospace heading stands over proportional prose in one view
(`test_a_role_takes_its_own_slot_and_size_inside_one_document`). The shipped
candidates are unchanged: Roboto in four styles or Droid Sans upright borrowing
Roboto's emphasis for the proportional slot; Inconsolata Medium and the vendored
Bold for the monospace one. Code blocks, diffs, tool output and arguments are
offered monospace only: their columns are content.

**A saved preset still means what it said.** `settings.load` reads a
`typeset_faces` (and the older `typeset_mono`) when the file says nothing about
roles or the family: `mono` is every role that may be monospace set so,
`roboto-large` is prose two pixels up, `droid` is the Droid Sans family, `roboto`
the defaults. `typeset.configure(faces=, mono=)`, `typeset.mono()`,
`typeset.DEFAULT_PRESET`, `--faces` and `--prose` are kept as those spellings
because the panes' tests and four scripts call them; `Preset`, `PRESETS`,
`presets()` and `preset()` are gone.

**§5's second item is refuted: a code span can be sized inside a paragraph, from
this tree, without rewriting a node.** The library's markdown reader asks the theme
what every run is set in (`Theme.font_for`), for a settled document and a stream
alike. The seam's theme is a subclass that answers a code span with its own size.
So `roboto-large`'s purpose — inline code level with the chrome — is met by the
default: `Role.INLINE_CODE` at step 0 is Inconsolata at the chrome's 16 px inside
16 px Roboto, where the block's em gave 14.3 px. Prose keeps its size and its
density. Whether a 16 px monospace span reads as *large* beside Roboto (its advance
is 8 px to Roboto's 7 or so) is a judgement for a person; the role's step is the
remedy, one pixel at a time.

**Line spacing is a table in code**, `typeset.LEADING`: a role's pitch as a multiple
of its own pixel height, taken to a whole pixel. Not a multiple of the em, which
differs by face at one height (Roboto 13.65 px, Inconsolata 15.25 px at 16). The
defaults are the lead's, for the operator's eye: 17 px at a 16 px size for prose,
code blocks, tool output, arguments and tables (it was 18.43 for prose and 19.07
for code and literal text), a heading at its own pixel height (22 px for an h1; it
was 21.6), a diff's row 18 px (it was 20) — the row is the library's `DiffView`
grid, which takes it from the theme it is handed, so it is in the same table, and
a diff fenced in a document is given the same theme.

What tightening needed, measured in `tests/test_typeset.py`: the library moves a
line down whenever its ink would touch the line above, and at 17 px the strut is
12.75 px over 4.25, which a bracket, an accented capital and every box-drawing
glyph reach past. Unhandled, lines of one paragraph were not one pitch apart and a
`tree` listing's rules did not join. The seam's metrics now measure no glyph taller
than the strut of the tightest entry in the table (the glyph is still drawn whole),
and with that every line of a block is one pitch under the last at 17 px and at
16 px, selection bands tile with no seam and no overlap, and no descender of one
line reaches the ascender of the next in either family at 16 px (they may touch).
Lines in a 600 px pane: prose 32 → 35, literal text 31 → 35, diff rows 30 → 33.

**Whose advances lay text out is one constant**, `typeset.METRICS`, for the same
reason leading is a table: `"hinted"` (what ships: ImGui's hinted advances) or
`"file"` (the faces loaded unhinted, the library's own metrics, nothing of its
measuring overridden). Both draw every view kind and pass the suite. See
`planning/2026-10-07-advtext-sets-the-text.md` §6 for the decision this is the
instrument for; `scripts/compare_metrics.py` puts the two side by side.

## 1. What was decided

*Superseded by §0. Kept as the record of what the presets were.*

**Selection is among presets, and a preset is data.** `ui/typeset.py` holds `PRESETS`; a
`Preset` is a name, a menu label, whether prose is monospace, an optional file for the
upright prose role, and a size for prose relative to the chrome. `Settings.typeset_faces`
names one and Text → Faces chooses it. There is no file picker and no system-font browser:
a preset's file is a path relative to imgui-bundle's fonts directory and a test refuses
anything else.

| preset | upright prose | bold · italic · bold italic · headings | code, diffs, literal | prose size |
|---|---|---|---|---|
| `roboto` (default) | Roboto Regular | Roboto, real faces | Inconsolata Medium / Bold | chrome's |
| `mono` | Inconsolata Medium | Inconsolata Bold; **italic is upright** — no monospace italic exists on disk or as an `advtext` role | same | chrome's |
| `roboto-large` | Roboto Regular | Roboto, real faces | same, at the chrome's size | **9/8 of the chrome's** |
| `droid` | **Droid Sans** | **Roboto** Bold, Italic and Bold Italic — Droid Sans ships in one style | same | chrome's |

Each fallback is pinned in `tests/test_typeset.py`
(`test_a_one_style_family_sets_upright_prose_and_borrows_the_rest_from_roboto`,
`test_the_monospace_setting_puts_every_role_in_inconsolata`).

**`typeset_mono` is gone from `Settings`; a file that has it still means what it said.**
`settings.load` reads `"typeset_mono": true` as `typeset_faces = "mono"` when no preset is
named. `typeset.configure(mono=)`, `typeset.mono()` and `--prose proportional|mono` are
kept as spellings of the first two presets, because five panes' tests and two scripts call
them.

**Text size is a multiplier, `Settings.typeset_scale`**, offered at 87.5 / 100 / 112.5 /
125 % (14, 16, 18, 20 px at a 16 px UI font). A multiplier and not an override so the text
still follows the size `hello_imgui` gives the UI font for the display. It scales typeset
text only; the chrome does not move.

## 2. Is there an honest third preset? Two, of different worth

*Superseded by §0: `roboto-large` is prose at step +2, and its reason for existing
is met at the default size by the inline-code role.*

**`roboto-large` is the honest one, and it answers the survey's open question 3.** At one
pixel height Roboto's em is 13.65 px and Inconsolata's 15.25 px, and a code span has its
paragraph's em, so inline code in Roboto prose is drawn 14.3 px tall beside 16 px chrome.
The library sizes a span by its block and has no per-face adjustment, so the code span
cannot be raised; the prose can. At 18 px Roboto's em is 15.36 px and Inconsolata at that
em is 16.1 px: the span is back on the chrome's raster. Fenced code, diffs and literal text
were already borrowed from the monospace theme and do not change. **The price is density**:
prose line pitch goes from 18.4 px to 20.7 px, 12 % fewer lines of prose in a pane. That
is why it is a preset and not the default. For scale: 20.6 px is already the prose pitch
of the `mono` preset, so `roboto-large` is as tall as all-Inconsolata prose and still fits
more characters on a line.

**`droid` is real and marginal.** It is the only other text face in the wheel. It is
visibly a different face — looser letterfit, a paragraph that wraps a few words earlier —
but it is a second humanist sans from the same foundry as Roboto, in one style, so every
heading and every emphasised run is Roboto beside it. Two families in one paragraph did
not read as broken at 16 px in the pictures; it is not a reason to pick it either. It also
is the one preset that costs something to switch to (§3). It is in the list because the
operator asked whether selection among shipped fonts was viable and this is the whole of
what "shipped" offers; it is one line of `PRESETS` to remove.

**Latin Modern Math is not a text face** and is not offered as one. It is loaded for the
glyphs Roboto lacks (`→ ✓`), which the library borrows from it.

## 3. Switching without a restart: measured, and it holds

Two separate questions, both run in a real window.

**Does a preset switch need the atlas at all? No.** `advtext` names faces by role and
keeps one file per role, so `droid` has to re-register `Face.PROSE`. `load_fonts` puts
every file any preset names on the atlas at start-up and remembers each by path; a switch
is then a registry change, a font looked up by path, and the seam's epoch. Nine switches
across all four presets and two scales, each made *inside* a frame the way a menu click
arrives:

| switch | `configure` | the frame that rebuilds | atlas fonts |
|---|---|---|---|
| among `roboto`, `mono`, `roboto-large`, or a scale change | 0.03 ms | 20–40 ms | 10 → 10 |
| to or from `droid` | 50–250 ms | 430–510 ms | 10 → 10 |

Every stage drew 656 glyphs in the markdown view with 0 missing; nothing was pending or
failed afterwards; returning to `roboto` gave a picture identical to the first. The
half-second for `droid` is the library re-reading every face's metrics after
`register_font` clears its caches — all of them, by its own design, the math face's
included. It is paid once per click and not again. Start-up is unchanged at 1.11 s with
Droid Sans added (ImGui 1.92 bakes on demand).

**Can a font be added to the atlas between frames under a real renderer?** This is the
thing `advtext/ui/fonts.py` says it had not checked. It works here: two files added in
`pre_new_frame` on frame 162 (one through `hello_imgui.load_font`, one through
`add_font_from_file_ttf`), atlas 10 → 12 in 0.33 ms, both drawn at 20 px in the same
frame with no tofu (`switch-8-…png`). The seam does not rely on it — nothing is added
after start-up — but a later preset whose file is too costly to preload has that route.

So the menu entries take effect on the click and say nothing about a restart.

## 4. What was looked at

`shots/fonts/switch-{0,1,2,3,6,7,8}-*.png` (driver `shots/fonts/switch_driver.py`; stages
4 and 5, the second visits to `roboto` and `droid`, were written and not opened): one markdown
document with bold, italic, bold-italic, inline code, a list, a quote, a fence and a table,
beside the six `Weight` faces as native lines, a literal block and wrapped plain prose,
under a line of chrome naming the stage.

- `0-roboto` / `8-…` — identical typeset panes: the round trip leaves nothing behind.
  Inline code is plainly shorter than the chrome line above the pane.
- `1-mono`, `7-mono-scale0875` — every role Inconsolata, bold a real bold, emphasis
  upright and so invisible; at 14 px still legible.
- `2-roboto-large` — `Settings.typeset_faces` in the paragraph is the size of the same
  words in the chrome line; the fenced block now reads slightly *smaller* than the prose
  around it, which is the same mismatch pointing the other way and milder.
- `3-droid` — Droid Sans upright, Roboto bold/italic in the same lines; the paragraph
  wraps at "Wa" where Roboto wraps at "kerning". No tofu; `→ ✓` still borrowed.
- `6-roboto-scale125` — everything typeset at 20 px, chrome at 16.

`shots/fonts/app-{roboto,scale125}.png`, `focus-{large,droid}.png`: the app itself through
`theme.load_fonts` with `--faces` / `--text-scale`, which is also the saved-setting path
(configured before the atlas exists). At 125 % the inbox and DETAIL diffs are larger, show
fewer rows in the same box, and nothing overlaps or clips that did not at 100 %.

Seen and not caused by this change: a native `typeset.label()` line in a prose weight draws
`→ ✓` as `�`. ImGui text has no fallback across fonts; only views laid out by the library
borrow a glyph.

## 5. Out of scope, by decision

**The chrome font** — rail, inbox rows, status bar — stays Inconsolata under every preset
and every scale. `inbox.py`'s `_X_*` columns are constants for a monospace advance; a
proportional chrome font is a layout change, not a font choice.

**A per-face size adjustment inside one paragraph.** It needs a size on a span or a scale
on a face in `advtext`, which is outside this tree. `roboto-large` is what can be done
from here. *(Refuted 2026-10-08: see §0. `Theme.font_for` is the size on a span.)*

## 6. Not verified

- No person has looked at any of this. The judgements in §2 and §4 are one reader's, of
  PNGs.
- Xvfb with llvmpipe at 1× is not a GPU, an LCD or a scaled display. The scale setting was
  only ever multiplied against a 16 px UI font in a window; 17 and 24 px are unit tests
  with the font size stubbed.
- The scale under every pane. TRIAGE and FOCUS were photographed at 125 %; BOARD and BRIEF
  were not opened at any scale but 100 %.
- A preset switch while a `Stream` is mid-arrival. Its rebuild on an epoch change is the
  path a face switch already took before presets; it was not re-photographed.
- The between-frames atlas add was one run, two fonts, one backend.

## 7. Open, for the operator

*Items 1 and 2 are closed by §0: Droid Sans is a family for the proportional slot
and not a preset, and nothing has to be the default to level inline code. Open
instead: the leading defaults (17 px, and 18 for a diff) are the lead's and are for
the operator's eye; and whether inline code at the chrome's height reads as large.*

1. **Does `droid` stay?** It is honest about what it is and it is the weakest entry. If the
   answer is no, the registry machinery it needs (`_apply_faces`, the remembered loader)
   is still what any font bundled later would use.
2. **Should `roboto-large` be the default?** It fixes the one thing in the default that
   looks like a mistake, for 12 % of the prose lines.
3. **Is 87.5–125 % the right range**, and should the chrome follow it? Today it does not,
   deliberately.
