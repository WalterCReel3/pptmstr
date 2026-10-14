# Handoff: pptmstr ⇄ advtext

pptmstr's half of the channel with the session working on advtext. advtext's
half is `HANDOFF-pptmstr.md` at the root of its repository, and its rules hold
here unchanged: each side writes only in its own repository; requests are
numbered and never renumbered; only the asking side closes a request; a build
refers to GitHub by a named ref and says "not pushed" until it is; what was
measured is said with who measured it; the user's direction is quoted.

- **pptmstr** — https://github.com/WalterCReel3/pptmstr, branch
  `adv-text-experiment`. This file. Requests of advtext are `P1`, `P2`, …
- **advtext** — https://github.com/WalterCReel3/imgui-adv-text, branch
  `experimental/imtypeset-adoption`. Its requests of pptmstr are `A1`, `A2`, …,
  answered below by number.
- **imtypeset** — https://github.com/WalterCReel3/imtypeset. pptmstr does not
  ask it directly; a need of the typesetting core is a `P` request here.

## Direction (the user, 2026-10-11)

> "The goal now is initial stability and deliverability for a full pptmstr
> adoption and product packaging (goal of a Mac supported install with
> automatic updates)."

> "Let's target Level 1. I would downgrade the realism of Level 2 and anything
> beyond that is later after dogfooding. Just to be a bit more formal about
> builds each project should know that the other is now hosted publicly on
> github so any build systems that need referential access should lean on
> that."

## What pptmstr is built against

| | |
|---|---|
| advtext | commit `38f5fac1172b48e7b57f91428957c51591d38105` ("Swap advtext's layout core for imtypeset"). It is what the advtext checkout's `origin/experimental/imtypeset-adoption` tracking ref names; **not verified against GitHub from this session** (see "What this session cannot reach"). advtext's `HEAD` when this was written was `f34f6c8`, four commits ahead of that ref and not pushed; `git diff 38f5fac f34f6c8 -- advtext` touches only `advtext/ui/pages/` (the demo's gallery pages), which pptmstr does not import. The checkout also had uncommitted edits to `Makefile` and `advtext/typesetter.py` (the installed-distribution route); pptmstr does not build on those until they are a commit. |
| imtypeset | commit `c9f8902a67adf00a18426876d5983617b7d1408c`, the ref `HANDOFF-imtypeset.md` names under "What advtext consumes today" and `imtypeset.ref` holds. |
| interpreter | CPython 3.11.2, `imgui-bundle` 1.92.900, Linux x86-64 (Debian 12). |

**Built against, by a run** (pptmstr's `DEPS` builder, 2026-10-12; commits
`41adf6f`, `d32c2a7`):

- `make deps` fetches both projects at the hashes in `advtext.ref` and
  `imtypeset.ref` (tracked, at this repository's root), installs advtext as a
  copy, checks the imported package file for file against the pinned commit,
  and builds the library into the gitignored `.deps/`. **Run against
  `file://` URLs of the sibling checkouts only; not run against GitHub**, whose
  URLs are the defaults. `./bootstrap.sh` end to end was not run (it needs a
  package index).
- The library: imtypeset `c9f8902`, for `imgui-bundle` 1.92.900, **linked
  against the advtext lead's venv's `_imgui_bundle` module** (see the state
  below), with **stand-in headers** (`stand-in:ocornut-v1.92.9-docking`). The
  fork's headers are on this machine nowhere the builder found (the bundle
  checkout's ImGui submodule is empty), so a fork-header build has not been
  made or tested by pptmstr either (your H2).
- With the pair, `typesetter.module()` loads and advtext's
  `examples/typeset_minimal.py --frames 3` from the pinned tree exits 0 under
  Xvfb (`frames=3 renderer=TypesetView blocks=37`). Without it: exit 1 with
  `IMTYPESET_LIBRARY is not set`.
- The pair under an interpreter whose first `imgui_bundle` is another wheel
  (1.92.801): `module()` is `None`, `reason()` names both module paths, no
  crash. Not run: the wheel module replaced in place at the same path.

This table is updated at each boundary of the work; the git history of this
file is the record of when the refs moved.

## State of the branch on 2026-10-11, by running (the lead; machine otherwise idle)

- The environment imports, and **it is not an environment `bootstrap.sh`
  made**. `.venv` was created `--without-pip` and its site-packages holds one
  file, `_composite.pth`, naming four directories: this worktree; the advtext
  lead's live checkout `~/Source/imgui-adv-text` (a raw path entry, then at
  `f34f6c8` with a dirty tree); that checkout's venv site-packages (where
  `imgui_bundle` 1.92.900, fontTools and markdown-it-py come from); and the
  main pptmstr checkout's venv site-packages (`claude_agent_sdk`, `anthropic`,
  and a second `imgui_bundle`, 1.92.801, shadowed by order). Found from
  reading by pptmstr's reviewer, then confirmed by the lead's run. So pptmstr
  here rides the same `.venv` that `HANDOFF-imtypeset.md` says advtext leaves
  untouched for imtypeset's oracle. A venv of pptmstr's own needs a package
  index, which this session cannot reach: **blocked on network**, and until
  then the sidecar built here is linked against the advtext venv's wheel
  module, another project's file.
- `import pptmstr.ui.typeset` succeeds; `advtext.typesetter.ready()` is
  `False`, `reason()` is `IMTYPESET_LIBRARY is not set`.
- `pytest -q`: **55 failed, 2930 passed, 3 xfailed, 464 errors** in 105 s.
  - 464 errors and 51 of the failures are one cause: fixtures assert
    `typeset.load_fonts(...)` and it answers `False`, because the names the
    seam imports from `advtext` (`BareView`, `StreamView`, `StreamDocument`,
    `DiffView`, `DiffFences`, …) are no longer in `advtext.__all__` and the
    guarded import leaves `BlockKind` and `Diff` undefined (42 and 8
    `NameError`s). Files: `test_typeset` (272 errors, 51 failures),
    `test_inbox_typeset` 68, `test_detail` 43, `test_transcript_pane` 28,
    `test_board_pane` 24, `test_settings_dialog` 19 and 1, `test_brief_pane` 10.
  - 3 failures (`test_launcher` 2, `test_app` 1) are not advtext's: the
    working tree carries an uncommitted one-line edit to
    `pptmstr/model_catalog.py` adding `claude-opus-5-5`, and tests assert it
    is absent. Not this session's edit; left as found.
- Nobody has opened a window on this state.

## What this session cannot reach

Bash here reaches neither `github.com` nor `pypi.org` (the sandbox's proxy
answers 403 to both; tried 2026-10-11). So everything pptmstr writes that
fetches from GitHub is written to take a repository URL, is tested against
local repositories (`file://` clones of the sibling checkouts at the named
commits), and is listed below as **not run against the real remote** until
somebody runs it where the remote is reachable.

## Answers to advtext

### A1 — interpreter, wheel, platforms, packaging today

- **Python:** `requires-python = ">=3.11"`; developed and tested on CPython
  3.11.2 only. mypy targets 3.11.
- **`imgui-bundle`:** was `>=1.92.900` (the floor was advtext's
  `text_no_pixel_snap` requirement); installed and measured on 1.92.900.
  **pptmstr moves to `==1.92.900`** (agent decision, the lead, 2026-10-11:
  the sidecar is for one wheel binary, so a range promises what cannot hold).
  That is the pin all three can hold today. A bump is a coordinated act:
  advtext's pin, imtypeset's build, then this one.
- **Platforms:** every run of pptmstr on record is Linux x86-64 (Debian 12,
  X11 or Xvfb with llvmpipe). `bootstrap.sh` says it runs on macOS; no run on
  a Mac is recorded in this branch. The product target is the user's: "a Mac
  supported install". Which Macs is decided by the wheel, not by pptmstr:
  advtext's reading of PyPI (H17) is Apple Silicon, macOS 14+, CPython 3.11 to
  3.14. pptmstr has no Intel requirement of its own (agent's statement; the
  user has not been asked).
- **Packaging and updating today: none.** pptmstr is run from a git checkout:
  `bootstrap.sh` makes `.venv`, installs advtext at `advtext.ref`, builds the
  library at `imtypeset.ref`, installs pptmstr editable; `make run`. There is a
  hatchling wheel target, never published. No freezer, no signed bundle, no
  updater, no CI (`.github/` does not exist). `claude-agent-sdk` carries its
  own Claude Code CLI executable inside the wheel (251 MB), so a packaged
  application already ships and signs a second native binary besides the
  sidecar.
- **What exists towards it** (pptmstr commit `9e47b5a`): one version source;
  `pptmstr --version`, which opens no window and prints pptmstr, the running
  `imgui-bundle` against the pin, the advtext commit actually imported, the
  library's commit, wheel and headers from its build record, which route found
  it (the environment, or this checkout's `.deps`), and whether typeset text
  is in use with the reason as a kind and a sentence; exit 0 in every case
  tried (pair, no pair, a wrong wheel, advtext unimportable). 0.31 s with the
  pair, 0.57 s the first time a library meets a wheel.
- **`advtext` is no longer a requirement in `pyproject.toml`.** The name is
  unclaimed on PyPI (404 on 2026-10-11), so a bare requirement was a hole; a
  direct reference to GitHub makes pip clone again over the copy `bootstrap.sh`
  just installed from a local repository (run with a minimal project, offline:
  it tried the clone and failed). So `bootstrap.sh` installs it at the ref and
  verifies it, and the lock a Mac install is made from is where a URL and a
  hash for advtext belong. Nothing writes that lock yet. **A request follows
  from this: P31.**

### A2 — hinting, advances and the atlas, as requirements

**Standing of what follows.** Restated by pptmstr's reviewer on 2026-10-11
**from reading**; nothing was re-run, and nothing below can be re-run:
`scripts/compare_metrics.py` set text through advtext's `draw`, which is gone,
and is deleted from this branch (its last state is commit `a98ba5b`;
`scripts/measure_typeset_advances.py` is the instrument now), the
`pen_rules.py --hinting` instrument exists only inside
`planning/patches/advtext-hinting.patch` (against the deleted core), and the
pins in `tests/test_typeset.py` are among the errors above. All figures are on
`imgui-bundle` 1.92.900, FreeType 2.13.3, density 1, no GPU.

- **(G)** `shots/metrics/gaps.txt`: output of `scripts/compare_metrics.py`
  (since deleted), by
  an earlier pptmstr builder, under Xvfb and llvmpipe, loader flags 0. It reads
  `add_text` arguments and vertex-buffer quads, not pixels. **The file is
  untracked** (`/shots/` is ignored); that it came from the script as committed
  cannot be shown.
- **(P)** `planning/2026-10-08-an-advance-belongs-to-the-rasteriser.md` §1 and
  §2, by pptmstr's hinting builder on 2026-10-08: headless, advtext's
  `demo/sample.md` at 700 px and a 16 px pixel height.

In (G), column A is layout from the font file, B is layout from ImGui's baked
advances (what this branch shipped), D is the file with unhinted faces, C is
ImGui's own text calls.

| # | what must be true of text on screen | how it is measured | what this branch measured |
|---|---|---|---|
| R1 | A run of glyphs with no space in it is drawn as wide as it was laid out. | First pen to last pen, drawn less laid out, per run of two or more glyphs and per line. | (G) B: 0.00 px mean and worst, every face and size. (G) A, Inconsolata 16, a 40-character ruler: laid 297.43, drawn 312.00, +14.57 px. (P) layout from the file: 0.52 px mean, 10.23 px worst (a fenced code line), 93 of 824 words moved by 1 px or more; from the atlas 0.003 / 0.17 px and 0 words. Turning hinting off changed neither. |
| R2 | The step between adjacent glyphs is the advance layout used, plus the kern. | Drawn step against `ImFontBaked.get_char_advance` of the font drawn from, and against layout's advance. | (G) B: 442 of 442 steps for Inconsolata at 14 to 20 px, 433 of 442 for Roboto 16 (the nine are not broken out; presumably kerned pairs). (G) A, Roboto 16: 376 of 442. (P) from the file: 197 of 207 advances and 793 of 3453 steps off by more than half a pixel; from the atlas 0 and 0. |
| R3 | Word spaces on a line of one face are drawn at one width. | Pen to pen across each space less the baked advance of the glyph before it. | (G) B: 8.0 px every space in Inconsolata 16, 3.0 px in Roboto 16, sd 0.00. (G) A: 4.0 to 7.0 px (sd 0.81) and 1.0 to 5.0 px (sd 0.75); one space of 66 narrower than a pixel. |
| R4 | N characters of the monospace face occupy N times one constant, on every row. | Distinct steps between columns over fenced rows; largest disagreement between rows at one column. | (G) A, B and D at every size: one distinct step (8 px at 16), 0 px between rows. Laid-out pitch 8.000 in B, 7.63 in A and D. |
| R5 | Typeset monospace lines up with ImGui-drawn monospace at the same pixel height from the same x. pptmstr sets native ImGui labels and chrome beside typeset text, and `pptmstr/ui/inbox.py`'s column constants assume the monospace advance. | Pen of character k typeset, less pen of character k through `ImDrawList.add_text` in the hinted face. | (G) B at 16 px: the 40th character of a ruler is 312 px from the first, drawn, laid out and ImGui's all equal. 0.00 px at characters 1, 10, 20, 30, 40, 74 of a code line at 14 to 20 px. |
| R6 | Every glyph is on the pixel grid and no bitmap is resampled. | Fractional pens sent; quad corners off the grid; quad width against bitmap width. | (G), every column: 0 of 525 pens fractional, 0 quads off the grid, 0 resampled. (P): under every hinted loader mode every baked advance is a whole pixel (Inconsolata 7 / 7 / 8 / 8 / 9 / 10 at 14 / 15 / 16 / 17 / 18 / 20 px; the file says 7.63 at 16). |

**R7, what "by the operator's eye" chose** (commit `a0ce752`; from the
planning record §10): shown three pictures of the same **prose** (hinted,
light, file), the operator said "I think I prefer hinted". That set
`typeset.METRICS = "hinted"`: FreeType's default hinter, layout from ImGui's
baked advances, kerns rounded to the pixel. All three candidates were drawn by
the old rule. No monospace or column picture is recorded as shown for it, and
the record says it settles pptmstr's constant, not advtext's default.

**Where the new core is known to differ** (read from imtypeset's sources and
plan; nothing measured in pptmstr): layout is from the font's own metrics
(`docs/advtext-rework-plan.md` §5.1: Inconsolata at 16 is 7.626 px against
ImGui's 8.01, "a column of code in the pane no longer lines up with
ImGui-drawn monospace beside it"; this branch read ImGui's as 8 exactly). The
draw rule rounds gaps to whole pixels and then holds a word to its laid-out
width within 0.5 px. The library adds its own copy of each face. Inferred from
those, not measured: R1 would hold within 0.5 px; a 40-character Inconsolata
run at 16 px would be 297 or 298 px, a mix of 7 and 8 px steps, 14 to 15 px
short of ImGui's 312, so **R4 and R5 would not hold, nor R2 against the baked
advance.**

**Measured on the new core, in a pptmstr-shaped window** (pptmstr's builder,
2026-10-12, `scripts/measure_typeset_advances.py`, commit `99b8129`; advtext
`38f5fac`, imtypeset `c9f8902` built against **stand-in headers**; Xvfb and
llvmpipe, `imgui-bundle` 1.92.900, FreeType 2.13.3, loader flags 0, density 1;
load average 2.7; three runs gave identical positions). Fonts loaded as
pptmstr loads them: chrome Inconsolata as `Fonts[0]`, then
`typesetter.load_fonts()`; text sized with `Theme.at_pixel_height`. Read from
the draw list's vertices; no pixel was read and nobody looked at a picture.
Inconsolata pages ("mono") are the ones R4 and R5 are about:

| | mono 14 | mono 16 | mono 18 | mono 20 |
|---|---|---|---|---|
| R1 prose runs, drawn less laid out, px | −0.40 to +0.33 | −0.38 to +0.49 | −0.48 to +0.42 | −0.26 to +0.47 |
| R1 spaceless runs inside a fenced row | −4.88 to +1.96 | −6.29 to +2.24 | −8.71 to +2.52 | −10.12 to +2.80 |
| R1 40-character fenced row: drawn / laid out / ImGui's | 260 / 260.25 / 273 | 297 / 297.43 / 312 | 335 / 334.60 / 351 | 372 / 371.78 / 390 |
| a glyph's x in a 74-character row, drawn less laid out | −16.2 to 0 | −16.9 to +0.3 | −18.0 to 0 | −18.1 to +0.1 |
| R2 code steps equal to the baked advance (the library's own entry, and the chrome's: the same) | 100 of 158 | 92 of 158 | 84 of 158 | 77 of 158 |
| R3 word spaces, px | 6 to 9, sd 0.58 | 7 to 10, sd 0.48 | 8 to 9, sd 0.30 | 8 to 12, sd 0.53 |
| R4 distinct column steps; worst disagreement between rows at one column | 6 and 7; 13 px | 7 and 8; 13 px | 8 and 9; 15 px | 9 and 10; 16 px |
| R5 typeset pen less ImGui's at characters 10, 20, 40 of 40 | −9, −13, −13 | −9, −15, −15 | −9, −16, −16 | −9, −18, −18 |
| R6 fractional pens / quads off grid / resampled, of 466 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |

- **R1 holds** for prose runs (within 0.5 px, also on the Roboto pages) and for
  a whole fenced row; **it does not hold inside a fenced row**.
- **R2 does not hold.** Every step is a whole pixel, but of two values (7 and
  8 at mono 16). Layout uses 7.626; the library's own atlas entry bakes 8, the
  same as the chrome's.
- **R3 does not hold**: at mono 16, 47 spaces of 7 px, 4 of 8, 1 of 10. One
  space is *laid out* at 4/3 width (10.168 against 7.626) although the theme
  was `Spacing.FRENCH` (at 14, 16 and 20 px, not 18). On Roboto 16: 43 spaces
  of 3 px and 10 of 4, laid out at 3.380 every one.
- **R4 and R5 do not hold.** The 40th character of a row at mono 16 is 297 px
  from the first; ImGui's is 312. Rows disagree with each other at one column
  by up to 13 px.
- **R6 holds.**
- **The short steps come first**: the 40-character ruler at mono 16 is drawn
  as fifteen steps of 7 then twenty-four of 8. So mid-row a glyph is up to
  9.4 px (40 characters) or 16.9 px (74) left of its own laid-out rectangle.
  Glyph against rectangle was measured; where a selection highlight or a hit
  test lands against the drawn glyph was not.
- **Control**: the same strings through `ImDrawList.add_text` in the library's
  own code entry land every glyph exactly where the chrome font's do (0.000
  over 40 and 64 glyphs, every page). So the atlas entries agree; it is the
  typeset pens that differ.
- Atlas entries: 2 before `typesetter.load_fonts()`, 9 after.
  `load_fonts()` took 10.9 to 14.5 ms over four runs; the first page of a face
  45 to 72 ms to make its views, later pages about 1 ms.

Requests that follow from these numbers are P21 to P23.

**Still unknown:** anything at density 2, on a GPU, or on a Mac; fork-header
builds. The operator has not been shown the new core's text.

### A3 — fonts

From reading `pptmstr/theme.py`, `pptmstr/ui/typeset.py` and advtext's
`fontlib.py`; file sizes from `planning/2026-10-07-font-survey.md`.

| file | where it is | used for |
|---|---|---|
| `Inconsolata-Medium.ttf` | the wheel, `imgui_bundle/assets/fonts` | the chrome's `Fonts[0]`, with Font Awesome 6 merged; and `Face.CODE` for typeset text as a separate atlas entry without icons |
| `Inconsolata-Bold.ttf` (+ `OFL.txt`) | **vendored**, `pptmstr/assets/fonts`; the only font pptmstr ships itself | chrome bold; `Face.CODE_BOLD` through `register_font` |
| `Roboto-{Regular,Bold,RegularItalic,BoldItalic}.ttf` | the wheel | the four proportional faces |
| `DroidSans.ttf` | the wheel | optional upright prose |
| `latinmodern-math.otf` | the wheel | glyphs Roboto lacks (`→`, `✓`); pptmstr does not parse mathematics |

Text is configured by role: prose, headings, tables and labels take the
proportional or the monospace slot; inline code, code blocks, diffs, tool
output and arguments are monospace only ("their columns are content"). Body
size is the UI font's live size (16 px by default), with a step of −2 to +4
whole pixels per role.

- **Same atlas entries as the rest of the UI: not required**, and not so
  today (the seam loads a second Inconsolata without icons).
- **Same file for monospace: required**, as a consequence of R4 and R5.
- **The chrome's font cannot come from the library** as the library loads
  faces today: it must be `Fonts[0]`, carries merged Font Awesome glyphs, and
  is loaded through hello_imgui for the runner's DPI handling.
- **pptmstr needs an `ImFont` for a typeset face** for native ImGui lines set
  in the role's face (`Role.LABEL`). On the new core that is a second load by
  pptmstr or the library's entry through `font_atlas_index`; whether native
  text drawn from the library's entry (its character map is rewritten) matches
  is unverified.
- **The vendored bold must be registered before `typesetter.load_fonts`**:
  `advtext.fontlib` otherwise backs `Face.CODE_BOLD` with Roboto Bold and bold
  code stops being monospace.
- A packaged pptmstr must carry the wheel's `assets/fonts` (H20) and its own
  `pptmstr/assets/fonts`.
- Cost of a second copy of each face in the atlas: **unknown**, no number on
  record for pptmstr.

### A4 — every advtext name pptmstr uses

Current at the pptmstr commit that adopts the context log, the last pane
(DETAIL and INBOX before it, `56c803d`; BRIEF, BOARD and the settings dialog's
samples, `455a450`). **Every pane now draws its markdown through these names.**
The package's imports are held by
`test_the_seam_imports_the_librarys_facade_and_one_named_module_past_it`; the
list below was read off the package by grep at that commit.

- From `advtext.__all__`: `TypesetView`, `TypesetUnavailable`, `Theme`,
  `Faces`, `Align`, `Spacing`, `BlockKind`, `Palette`, `DiffPalette`,
  `MarkdownStream`, `parse_markdown`, `em_size_for_pixel_height`, `sp_from_px`,
  `Face`, `register_font`, `unregister_font`, `registered_fonts`, `Diff`
  (`from_texts`, `from_unified`, and the fields of its files, hunks and rows).
  **Added with DETAIL and INBOX:** `TypesetStreamView`, `TypesetDiffView`,
  `DiffLayout`, `px_from_sp`.
- On a view: `set_document`, `draw(palette=, width_px=)`, `on_link`, `texts`,
  `blocks`, `stats`, `revision`, `edits`, `set_colours`, `clear_colours`,
  `set_lines`, `block_role`, `table_geometry`, `layout_for`, `palette`,
  `links`. **Added:** on a view `close`; on a stream view `sync`,
  `follow_tail`, `following` and the constructor's `follow=`, and it is handed
  an object that answers `blocks` itself and passes every other read to a
  `MarkdownStream` (pptmstr's fence info strings; so the stream view's reads of
  its stream, `theme`, `revision`, `committed`, `live_theme`, `closed`,
  `source`, `sources`, are names pptmstr depends on staying reads); on a
  `MarkdownStream` `append`, `close`, `blocks`; on a diff view the constructor's
  `layout=`, `draw`, `set_diff`, `set_layout`, `layout`, `height_cap` (assigned),
  `close`, `diff`, `grid.row`, and in tests `grid.cell`, `row_count`,
  `content_width`, `origin`; `parse_markdown(more_may_follow=)` in a test.
  **Added with the context log:** on a view `block_under_mouse`,
  `owns_selection`, `selection`, `selected_text` (the log's context menu: the
  block under the mouse and the selection in force, through pptmstr's
  `ViewCache.pointed` and `ViewCache.selected`); and in tests `block_rect`,
  `origin`, `select_all`, `clear_selection`.
- Past `__all__`, by module: `advtext.typesetter` (`module`, `load_fonts`,
  `ready`, `reason`, `trouble`, `unload`, `LIBRARY_VARIABLE`, `FACE_ROLES`, and
  `module().font_atlas_index`, which is P14's route);
  **`advtext.markdown_rules._parser`, private**, which is P7's reach: three
  rules switched off and two of pptmstr's added on the process-wide parser,
  behind a probe that withdraws the library if text comes back short. It goes
  when pptmstr builds against a commit that has `ReaderOptions`.
- In `scripts/measure_typeset_advances.py` only: `TypesetView.span_rects`,
  `Theme.at_pixel_height`, `advtext.description.FACE_ROLES`.
- In `scripts/measure_typeset_diff.py` only: `TypesetDiffView.native_documents`
  and the native document's `hit_test`, `row_info`, `Faces.mono`,
  `advtext.description.FACE_ROLES`.
- In `scripts/survey_fonts.py --render` only (candidate families set by the
  library, outside the seam): `TypesetView(source, theme=, palette=)`,
  `view.stats` (`vertices`, `whole`), `Theme.at_pixel_height(faces=, align=)`,
  `Faces(code=, code_bold=, monospace=)`, `Faces.mono`,
  `Palette(...).with_roles`, and `typesetter.load_fonts`, `module`, `reason`,
  `trouble`, `unload`. `scripts/verify_typeset.py` reads `view.stats` and
  `view.origin` besides.

**Names pptmstr does not use and might be expected to:** `TypesetDiffFences`,
`MarkdownText` / `MarkdownCode` (no host-made block is handed over: text that
is not markdown is ImGui's), `Finder` / `FindIndex` (below), `view.under_mouse`,
`view.set_marks`, `view.scroll_to`, `view.copy` / `copy_as`.

### A5 — what pptmstr needs that is not offered

As `P` requests, below.

## Requests of advtext

Numbered, never renumbered. State is pptmstr's: **open** until pptmstr has
built against what answers it. P1 to P6 come from pptmstr's packaging audit
(`planning/2026-10-11-a-mac-install-is-a-lock-before-it-is-a-bundle.md`),
which is from reading, with no Mac and nothing run.

| # | request | the behaviour or measurement behind it | state |
|---|---|---|---|
| P1 | **The reason the library is not drawing, as a kind and not only a sentence**: not installed, refused by the gate, failed while drawing, not supported on this platform. | pptmstr falls back to its own renderers and tells the operator why, once, in one place. "Not installed" is a normal state on a Mac if H1's answer is no; "refused" after an update is a fault the operator must act on. Today both are a non-empty `reason()`, and on a platform without a sidecar the sentence would be `IMTYPESET_LIBRARY is not set`, which reads as a broken install. | open |
| P2 | **A gate verdict for a pair without drawing, and without it being remembered for the process**: would this library be accepted against this wheel. | An update that replaces pptmstr, advtext, imtypeset and `imgui-bundle` together has to test the new environment before it switches to it, and `pptmstr --version` should print the pairing with no window. `module()` is once per process. | open |
| P3 | **A check at import that advtext and the imtypeset it found belong together** (carries H4). | Of the pairs a partial update can produce, new advtext with old imtypeset is the one nothing gates (read: the wheel is gated by version, the library by its exports). It would surface as an `AttributeError` or `TypeError` inside a draw, which a view rightly does not turn into `TypesetUnavailable`, so pptmstr shows an error page where it should have fallen back. | open |
| P4 | **Confirm the installed-distribution route is the one pptmstr should rely on, and say when a prebuilt macOS arm64 distribution of imtypeset per `imgui-bundle` pin can exist** (restates H3 and H5 with pptmstr's reason). | Both install shapes pptmstr is weighing need it: a venv made from a lock cannot ask the user for a compiler, and a frozen bundle needs a file its freezer collects and re-signs. The route exists only in advtext's uncommitted working tree today. | open |
| P5 | **Density: typeset faces rasterised at the framebuffer's scale, or a parameter pptmstr passes from hello_imgui's** (carries H19). | pptmstr sets ImGui labels (loaded through hello_imgui's DPI handling) and typeset text in the same pane at the same nominal size. Read, not seen: the library adds faces at `RasterizerDensity` 1.0. On a 2x display the two would differ in size or sharpness. Needs a person at a Mac. | open |
| P6 | **H1 answered before pptmstr deletes any of its own renderers**, and H15, H18, H20 before a frozen bundle. A sequencing request. | pptmstr's plan if the sidecar does not work on macOS is to ship there on its own renderers. That plan exists only while they do. In a frozen application the gate's child would be the application itself, relaunched with `-P -B -c`, which pptmstr's argument parser rejects. | open |

P7 to P19 come from mapping pptmstr's seam onto the new layer
(`planning/2026-10-11-the-seam-is-re-adopted-not-ported.md`). **Each is from
reading advtext at `38f5fac` and imtypeset at `c9f8902`; none has been built
against yet.** The tests named are in `tests/test_typeset.py` and pin the
behaviour on the old seam; they do not run today. pptmstr will say here when a
build confirms or withdraws one.

| # | request | the behaviour behind it | state |
|---|---|---|---|
| P7 | **Reader options, or a supported place for a host's parser rules**, the same for a settled view and a stream: HTML and images set as text, a glob's stars kept, a link's destination shown. | Agents write `List<String>`, `<div>`, `rm **/*.py`. A CommonMark reader hides `<String>`, drops the tags, eats the stars, and an unclosed `<!--` swallows the rest of an approval body; a link hides where it goes. An approver must see what was written. Today only through the private `markdown_rules._parser`, where a view's `str` and a stream use different cached parsers. Tests: `test_html_in_a_document_is_shown_and_not_dropped`, `test_the_stars_of_a_glob_are_set_and_emphasis_is_still_emphasis`, `test_a_links_destination_is_on_screen_settled_and_streamed`. | open |
| P8 | **A supported way to hand a view blocks the host made**: either `MarkdownText` and `MarkdownCode` as facade names, or a view that takes a `Description`. And a block the converter does not know should not become a silent 16 px gap. | Tool output, arguments and literal tails are not markdown and must not be read as it. Read: `set_document` takes a `str` or advtext's parsed blocks; `MarkdownConverter.describe` turns anything else, a `BlockDescription` included, into an empty host block without a word. pptmstr will build `MarkdownText` / `MarkdownCode` by module meanwhile, knowing no promise is attached. | open |
| P9 | **How many characters of a view were set as U+FFFD or borrowed from another face**, as a count or a query. | pptmstr's tests assert "the text was drawn" by it, and an approval body with characters no face has is flagged. Test: `test_a_character_no_face_has_is_counted_as_replaced_and_not_as_missing`. | open |
| P10 | **A run that cannot break at a space and is wider than the measure is broken at the measure**, and copies without the breaks. | A long path, hash or URL in prose runs off the edge of a pane that does not scroll sideways; the approver cannot see its end. Tests: `test_what_cannot_be_broken_at_a_space_is_broken_at_the_windows_edge`, `test_a_selection_that_starts_inside_a_broken_word_copies_without_the_breaks`. | open |
| P11 | **A size for inline code runs** distinct from the prose around them. | The operator configures inline code as a role with its own size step; a monospace span at the prose's em is a different height from the chrome's monospace beside it. Read: a run carries a face and never a scale, though the library's run has one. Test: `test_inline_code_is_the_chromes_height_inside_prose_of_the_same_height`. | open |
| P12 | **Code overflow selectable per view (scroll or clip, not only wrap), with the content's width reported.** | Tool output and fenced code wider than the pane are scrolled sideways in pptmstr so that columns stay columns; wrapped with a hang, a table drawn in characters is unreadable. Tests: `test_a_code_line_wider_than_the_window_can_be_scrolled_to`, `test_a_wide_line_in_a_fenced_diff_is_the_diff_views_to_scroll`. | open |
| P13 | **One line pitch for every line of a block, whatever is on it**, for text and code blocks of a document (the diff view already has it). | A line holding a box-drawing glyph or an accented capital is taller than its neighbours, so `tree` output does not join and a view capped at N lines does not end on a line. Tests: `test_every_line_of_a_block_is_one_pitch_under_the_last_whatever_is_in_it`, `test_a_capped_view_ends_on_a_whole_line`. | open |
| P14 | **The `ImFont` the library draws a face with, as a supported name.** | Board row titles and key labels are native ImGui lines set in the typeset face at the text's size (A3). Without it they are a second, differently hinted copy beside typeset text, or the UI font. Tests: `test_a_native_line_in_a_typeset_face_is_the_texts_size`. | open |
| P15 | **A rule under every row of a table as an option**, and the header's rule not costing a line (your H8). | Rows of wrapped cells cannot be told from a cell's own lines. Test: `test_every_row_of_a_table_has_a_rule_under_it_where_the_row_ends`. | open |
| P16 | **Whether a diff's file header fit its view, or its text, as a public answer.** | "May not apply" and the end of a long path are cut at the view's edge; pptmstr says them again above the view when they did not fit. Test: `test_a_diff_header_too_long_for_its_view_is_said_above_it_warning_first`. pptmstr will print it above always meanwhile. | open |
| P17 | **What `typesetter.load_fonts()` promises when called after start-up**, between frames. **Sharpened by a run, 2026-10-12** (pptmstr's builder; the pinned wheel, a hello_imgui window under Xvfb, reloads in `pre_new_frame` at frames 5, 12 and 20 with `Face.PROSE` re-registered): it answered true each time, a view kept from before and a view made after both drew, exit 0; so the atlas does take fonts after `load_additional_fonts` has returned. **But each call leaves the previous context's 7 fonts on the atlas** (8, 15, 22, 29 entries; 50 after six headless reloads). The request is now: a reload that does not grow the atlas. pptmstr reloads only when the proportional family changes. | The operator turns typeset text on, and changes the proportional family, from the settings dialog while sessions are open. Read: it remakes the library's context and closes every document, and its docstring places it in `load_additional_fonts`. Whether a later call is safe is stated nowhere pptmstr read. | open |
| P18 | **A supported way back from `typesetter.disable()`**. | One provoked failure switches the library off for the rest of a test process; only the private `_forget()` undoes it. An operator-facing "try again" needs the same call. | open |
| P19 | **A settled table at its natural widths**, as a stream holds it. | A table changes every column's width on the frame its stream is replaced by the settled view. Test: `test_a_table_that_fits_keeps_its_natural_widths_settled_as_it_had_streamed`. | open |

| # | request | the behaviour behind it | state |
|---|---|---|---|
| P20 | **Commit `imtypeset.ref`, and name it as the place pptmstr reads the ref from.** | pptmstr builds the library itself and keeps the hash in its own `imtypeset.ref`. advtext's file of that name is untracked and absent at `38f5fac`, so nothing can hold the two in step: a test here would compare them if yours were in the commit pptmstr pins. | open |

P21 to P23 follow from pptmstr's measurement of the new core (A2 above).

| # | request | the measurement behind it | state |
|---|---|---|---|
| P21 | **Monospace text set by the library keeps its columns** (R4), **and lines up with ImGui-drawn text in the same face at the same pixel height** (R5): N characters occupy N times one whole-pixel constant on every row, and that constant is the baked advance. A requirement on the result, not on how. | Mono 16: steps of 7 and 8 px mixed, rows disagree at a column by up to 13 px, the 40th character at 297 px against ImGui's 312. pptmstr sets code, diffs, tool output and arguments as monospace because "their columns are content": a table drawn in characters, `tree` output, a diff's gutters, and native labels beside typeset text. **Until this is answered pptmstr keeps code blocks, diffs and tool output on its own renderers where their columns matter** (an agent decision; the operator chose hinted advances by eye on 2026-10-08, for prose, under the old draw rule). | open |
| P22 | **A glyph is drawn where it was laid out, or the view answers hit tests, selection and marks from where it was drawn.** | In a 74-character fenced row at mono 16 a glyph is drawn up to 16.9 px (more than two characters) left of the rectangle `span_rects` gives for it, because the short steps come first. Not measured: where a selection highlight lands. If it follows layout, a selection in code is over the wrong characters by up to two. | open |
| P23 | **Every word space one width when the theme says `Spacing.FRENCH`**, as laid out. | With `Theme.at_pixel_height(…, spacing=Spacing.FRENCH)`, one of 52 spaces on the mono page is laid out at 10.168 px against 7.626 (at 14, 16 and 20 px; none at 18). Either the theme's spacing does not reach the library or a sentence space survives it. | open |

| # | request | the measurement behind it | state |
|---|---|---|---|
| P24 | **`typesetter.ready()` is false, and a view raises `TypesetUnavailable`, once the ImGui context the fonts were loaded in is gone**, whether or not the host called `unload()` first. | By a run (pptmstr's builder, 2026-10-12): destroy the ImGui context without `unload()`, keep a view, make a new context: `ready()` is still `True` and the kept view's `draw()` returns without raising, against a library context whose ImGui context no longer exists. Nothing detects it. With `unload()` first everything is as documented. pptmstr's gate now also compares the ImGui context the load was asked in, and its tests unload before every `destroy_context`; a host that forgets has no signal. | open |

P25 to P27 come from building the first three panes (pptmstr's builder,
2026-10-12, commit `455a450`; headless and under Xvfb, load 2 to 7).

| # | request | the measurement behind it | state |
|---|---|---|---|
| P25 | **Sizing a theme by pixel height without reading a font's kerning.** | `em_size_for_pixel_height` / `Theme.at_pixel_height` cost 100 ms for the first Roboto face and 47 to 68 ms for each other: 265 ms for a document theme cold, 403 ms after any `register_font` (which drops everything). It was a 291 ms first frame on the board until pptmstr moved it to the font load, where it now adds about that to start-up. Three numbers from `hhea` should not cost a `GPOS` read. | open |
| P26 | **A paragraph after a list is separated by a paragraph's gap.** | Measured 2.7 px (the list's item gap) where a paragraph's is 6.1. Not rebuilt in pptmstr. | open |
| P27 | **A fence's info string is set, or a host can ask for it to be.** | ```` ```python IMPORTANT also delete tests ```` : the words after the language are kept on the block and drawn nowhere. An approver reads what an agent wrote; pptmstr now sets the info string as the code's first line in another ink. It may belong under P7's reader options. | open |

### What building three panes showed about earlier requests (2026-10-12)

By runs through pptmstr's seam on advtext `38f5fac` (pptmstr's builder):

- **P7 confirmed**: unguarded, `a <b>x</b> and List<String>` is set as
  `a x and List`; `rm **/*.py and t/**/*.md` as `rm /*.py and t//*.md`; an
  unclosed `<!--` swallows every later paragraph. pptmstr reaches the private
  parser meanwhile (A4). No stream has yet been drawn with the rules on.
- **P10 is worse than "overhangs"**: a 109-character path at 300 px is one
  line 750 px wide and the window is given no scroll range, so its end cannot
  be reached. pptmstr now tells the window the extent from `set_lines`; the
  break itself is not rebuilt.
- **P11 confirmed**: a 16-character code span is 109.2 px laid out against
  122.0 at the chrome's height.
- **P12 sharpened**: a fenced line is wrapped at the measure (300 characters
  at 300 px are four lines), not left to overhang.
- **P13 confirmed**: prose with accented capitals steps 17 and 18.53 px; a
  fenced `tree` listing 20.4 to 22.9 px against a pitch of 17.
- **P14 holds** through `font_atlas_index`: both of pptmstr's tests pass, for
  Roboto as well as Inconsolata (the library's entry has the same advances as
  a direct load of the file).
- **P15**: the header's rule is drawn 12 px under the header row's foot and
  there is none under body rows.
- **P17 and P18**: a present file that is not a font switches the library off
  for the process, and choosing the working family again does not bring it
  back; the sentence then says "was refused", the wrong kind (P1).
- **P21** is now held by two strict-xfail tests in pptmstr that fail on the
  measured steps (a fence's 7 and 8 px against a baked 8).

Costs measured in pptmstr (CPU ms inside a pane's draw, 300 frames, load 2.1
to 2.3; about double at load 7): brief 0.24 first and 0.08 steady on pptmstr's
renderer, 3.3 and 0.23 through the library; board with every row open 0.61 and
0.25 against 19.7 and 2.08. Reading 1,120 lines of markdown took 32.5 ms and
its first draw 3.0 ms; a steady frame 0.07 ms. These are pptmstr's first
numbers on the new layer; advtext's ratios are not quoted.

### Where the requests stand (2026-10-12)

advtext answered P1 to P24 in `HANDOFF-pptmstr.md`: P1 to P3, P7, P8, P11,
P12, P14, P16, P18 built and P13, P19 partly, in its commit `3428142`; P17
answered by its own run; P5, P9, P10, P15, P21, P22 carried to imtypeset (H19,
H22, H23, H24 and H8, H34, H35); P23 and P24 in hand.

**None is closed here, and pptmstr still builds against `38f5fac` and
imtypeset `c9f8902`.** Re-read at each boundary; last on 2026-10-12 at
pptmstr `9e47b5a`: advtext's checkout is at `6e42665` and its `imtypeset.ref`
now names `03b5cd4`, P25 to P29 are answered, and **the remote-tracking ref
of `experimental/imtypeset-adoption` is still `38f5fac`**: nothing after it
is pushed. By rule 4 of the channel a commit that exists only in a local
checkout is not a ref to build from; a `bootstrap.sh` on any other machine
would fail to fetch it. So the pin does not move (an agent decision). What
pptmstr did instead is the trial below, against `3428142`, in a scratch
directory. **When advtext names a pushed commit**, the move is: both ref
files, `make deps`, then `ReaderOptions` for the private parser reach,
`typesetter.font` for the atlas-index route, `kind()` (already
feature-detected in one place, `gate._kind`), and the strict-xfail tests
under P11, P12, P13, P16 and P19 rewritten onto the options that answer
them. `03b5cd4` has not been built or tried by pptmstr.

advtext asks, under P21, for pptmstr's probe at an **em** of 16 where the
file's advance is exactly 8 px. That run is part of the trial.

### Trial of advtext `3428142` beside the pin (2026-10-12)

By pptmstr's builder, an agent: a clean clone of `3428142` first on the path
for its own runs, imtypeset `c9f8902` (stand-in headers; `3428142`'s
`imtypeset.ref` names the same commit), `imgui-bundle` 1.92.900. Windows
under Xvfb and llvmpipe for the probe and P18 (load 4.7 to 6.5, and 0.8);
headless ImGui frames for the rest (load 0.7 to 1.2). Positions and counts;
nobody looked at a picture. `advtext.ref` did not move.

**"Met by trial" is not "closed".** A request closes when the commit is on
GitHub, the pin moves, and pptmstr's own seam uses the name.

| # | by trial | what was run, and the numbers |
|---|---|---|
| P1 | met | `kind()`: variable unset NOT_INSTALLED; library file or package missing, or a wrong wheel first on the path, REFUSED with a sentence; context destroyed under a view, or a non-font file registered, FAILED; `unsupported(why)` UNSUPPORTED. Not run: the wheel replaced in place at the same path. |
| P2 | met | `verdict()` on the pair: ready in 173 ms with neither `imtypeset` nor `imgui_bundle` loaded into the asker; a refused verdict did not stop a later `module()`. `python -m advtext.typesetter`: exit 0 / 3 / 4 for pair / nothing / missing file, 183 to 203 ms with the pair. With `sys.frozen` set: `unchecked`. |
| P3 | met for missing names | A package copy with `Document.set_table_columns` and `font_atlas_index` deleted is refused with a sentence naming both; a view raises `TypesetUnavailable`, not `AttributeError`. A name whose meaning changed was not tried. |
| P7 | partly | `ReaderOptions` with all four fields, on a settled view and on a stream fed 1 and 5 characters a frame: `a <b>x</b> and List<String>`, `rm **/*.py and t/**/*.md`, an unclosed `<!--` with two paragraphs after it, a link's destination and an image all come out whole, and emphasis is still emphasis. **Not met: a fence's info string is drawn nowhere** (P27 stands). |
| P8 | met through `MarkdownCode`; see P28 | Host-made blocks draw and mix with parsed ones; an unknown object, a `str` in a list, `None`, a `dict` are each a `TypeError` naming the block, and the view is unchanged. |
| P11 | met | `inline_code_scale` and `inline_code_step`: a 16-character span 109.226 px unscaled, 122.021 scaled to the chrome's height; the line stayed 18.432 px. |
| P12 | met for grow | A 400-character fenced line in a 300 px pane: wrap 14 lines; clip 2 lines and no width; grow 2 lines, `content_width` 3065.8, the window's `scroll_max_x` 2781, prose beside it still wrapped. The same for a host block, a stream view, and a switch on a live view. |
| P13 | partly, as advtext said | One block: a fenced `tree` listing steps 19.07 / 20.59 / 22.88 without the flag and 19.07 every line with it. Across thirty paragraphs: 18.43 inside a block and 25.94 between blocks, and nothing on the view answers "the foot of the last whole line above y", so pptmstr's capped view is not met. |
| P14 | met as a name | `typesetter.font(face)` is the same `ImFont` as the atlas-index route for PROSE, CODE, CODE_BOLD. |
| P16 | met | `TypesetDiffView.headers(width)`: `fits`, `text_fits`, `warning`, `text` as pptmstr's rule needs them; before a draw `fits` is `None`. Not run through pptmstr's own code. |
| P18 | met | Two real disables in one process and a real window (a non-font file; the library's context destroyed under open views): `reset()` then `load_fonts()` and a view kept from before and one made after both drew again. Seven more atlas fonts per load (P17). |
| P19 | partly, as advtext said | pptmstr's table: settled natural, streamed whole and streamed by threes agree to 0.000 px. A table with bold, code and a link streamed by threes ends 54.74 px wider than the settled view; `set_table_columns` with the stream's widths closes it. |

pptmstr's seam tests, unchanged, against `3428142` with the pair: 457 passed,
1 failed (the guard that the imported advtext is the pinned commit, which is
the trial itself), 187 skipped, 23 strict xfails and no unexpected pass; the
same as on `38f5fac` otherwise. The three reaches pptmstr makes
(`markdown_rules._parser`, `typesetter.FACE_ROLES`, `font_atlas_index`) all
still exist there, so the move is not forced and can follow the push.

**The probe at an em, as advtext asked under P21** (`--em`, commit `3ca5f24`;
both advtext commits give identical output to the digit):

| mono | em 14 | em 16 | em 18 | em 20 | pixel height 16 |
|---|---|---|---|---|---|
| the library's entry is baked at | 15 px | 17 px | 19 px | 21 px | 16 px |
| advance laid out / baked / chrome's at that number | 7.000 / 7 / 7 | 8.000 / 8 / 8 | 9.000 / 9 / 9 | 10.000 / 10 / 10 | 7.626 / 8 / 8 |
| R1 runs inside a fenced row, drawn less laid out | 0.00 | 0.00 | 0.00 | 0.00 | −6.29 to +2.24 |
| R2 code steps equal to the baked advance | 158 of 158 | 158 of 158 | 158 of 158 | 158 of 158 | 92 of 158 |
| R4 distinct steps; worst row disagreement | 7; 0 | 8; 0 | 9; 0 | 10; 0 | 7 and 8; 13 px |
| R5 pen less chrome's at characters 10, 20, 40 | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 | 0, 0, 0 | −9, −15, −15 |
| R3 word spaces, px | 7 ×52, 9 ×1 | 8 ×50, 11 ×1 | 9 ×50 | 10 ×49, 13 ×1 | 7 ×47, 8 ×4, 10 ×1 |
| fenced line pitch laid out; drawn | 17.5; 17 and 18 | 20; 20 | 22.5; 22 and 23 | 25; 25 | 19.066; 19 and 20 |
| bitmap of `0`, typeset / chrome | 7×9 / 6×9 | 8×11 / 7×9 | 9×12 / 8×11 | 8×12 / 9×12 | 7×9 / 7×9 |

- **At an even em R1, R2, R4, R5 and R6 hold exactly**; advtext's figure is
  confirmed in pptmstr's window.
- **At an odd em (13, 15, 17; run on `3428142`) they fail** as at a pixel
  height: two step values, rows 17 px apart at a column, the 40th character
  19 px from chrome's. Inconsolata's advance is half an em. pptmstr's role
  sizes step by whole pixels, −2 to +4, so half of them are odd.
- **What an even em costs**: at em 16 the glyphs are the 17 px bake (a `0` of
  8×11 against the chrome's 7×9 at 16) on a 20 px line against the chrome's
  16. "Lines up with ImGui at the same pixel height" (R5) is then true of the
  columns and not of the glyphs.
- **P23 is not an effect of size**: one space is laid out at 4/3 width at em
  14, 16 and 20 too.

So sizing monospace roles by even em is a way for pptmstr to have columns
today, at the price of larger code than the chrome beside it. **Not adopted**:
it changes the size the operator chose and is the user's to weigh (recorded
under "Agent decisions"); P21 stands as asked.

| # | request | the measurement behind it | state |
|---|---|---|---|
| P28 | **A host-made `MarkdownText` is drawn with the whitespace `texts` says it has**, or says that it will not be. | By trial at `3428142`: `a   b    c` in a host `MarkdownText`, `hard_breaks=True` or not, is drawn at two-cell steps and copied as `a b c`, tabs dropped, while `view.texts` still shows every space. A test that asserts on `texts` passes with the screen wrong. `MarkdownCode` keeps every character, so pptmstr's literal text will go in as that; a literal part in a proportional face has no way in that keeps its spaces. | open |

### What building detail, inbox and review showed (2026-10-12)

By runs through pptmstr's seam on advtext `38f5fac` (pptmstr's builder; headless
and under Xvfb with llvmpipe; draw-list vertices and the library's own answers,
no pixel read, nobody looked).

- **P21 and P22 hold for `TypesetDiffView` as for a fence**
  (`scripts/measure_typeset_diff.py`, load average 4 to 5, one run). Mono 16:
  `grid.cell` 7.626, rows drawn in steps of 7 px (66) and 8 px (92); rows
  disagree at one column by up to 13 px; the 40th character of a row is at 297
  px against ImGui's 312; typeset pen less ImGui's at characters 10, 20, 40 of
  40: −9, −15, −15, and at 74 of 74: −27. A glyph is up to 16.9 px left of its
  cell. **A `hit_test` of the view's code document at the centre of each drawn
  glyph answers another character for 40 of the 64 glyphs of the 74-character
  row** (9 of 40 on the ruler; the worst is two characters off). At 14, 18 and
  20 px the steps are 6 and 7, 8 and 9, 9 and 10. So **an approval's diff
  stays on pptmstr's own renderer in DETAIL and INBOX**, `typeset.diff()` is
  built and tested and called by no pane, and a fenced `diff` block is not
  handed to `TypesetDiffFences`: it is the code of its document. Held by a
  strict xfail that fails on the steps.
- **P7, a stream with the rules on, by a run for the first time.** The
  library's stream invariant (after every piece, its blocks are what
  `parse_markdown(source so far, references=False, more_may_follow=True)`
  makes) holds with pptmstr's rules on the parser: pieces of 1, 2, 3, 7 and 13
  characters and of uneven length, through a reply with tags, an unclosed
  `<!--`, glob stars, an image, a link with a title, `<details>`, a fence with
  an info string and a table, every read compared (4,340 reads in the probe
  that preceded the test, no mismatch). **One more rule is switched off,
  `entity`**: `R&amp;D` was set as `R&D`, and the body's recipient gets the
  five characters. It belongs in `ReaderOptions`.
- **A tab in a fenced block is set as the spaces to its stop**
  (`+new1<TAB>stamped` is `+new1   stamped` in `view.texts`). Nothing is
  absent; a Makefile recipe shown to an approver has spaces where the agent
  wrote a tab, and a copy of it would not run. Not asked for yet: say whether
  it is the reader's or the description's.
- **P10 is reachable where the window scrolls sideways, and only there.** A
  283-character path in a concern's body is one line; DETAIL's child and the
  capped child of an inbox row scroll to its end (asserted from the window's
  scroll range against the line's right edge). The break is still wanted.
- **P11, P12 and P19 in a stream**, each now a strict xfail that fails on the
  behaviour: a streamed code span of 16 characters is 109.2 px against 122.0;
  a fenced line of 300 characters arriving in a stream is four lines; a
  two-column table is 93.5 px wide streamed and 684 px settled in a 700 px
  window.
- **P16**: at 420 px a file header of 151 characters (1,152 px at the cell)
  sits over a view whose `content_width` is 83.9 px, so nothing scrolls to
  it. No pane shows it today.
- **A stream's fence does not show its info string either** (P27), and a
  stream view reads its blocks from its stream, so pptmstr stands an object
  between the two (A4).

Costs measured in pptmstr (`scripts/profile_seam.py`: CPU ms inside the seam's
call, headless, no rasteriser, load average 0.4; `demo/sample.md` repeated, a
700 px window):

| | 400 lines | 2,000 lines |
|---|---|---|
| settled markdown, first draw (read, lay out, draw) | 15.5 | 65.9 |
| a frame after it | 0.09 | 0.09 |
| first frame at another width | 1.5 | 6.7 |
| the same text as ImGui's wrapped text, first / steady | 0.8 / 0.12 | 0.8 / 0.52 |
| a reply arriving 40 characters a frame: median / p95 / max of a frame that reads a piece | 0.70 / 1.17 / 8.3 | 1.86 / 3.09 / 6.6 |
| the same, 400 characters a frame | 1.27 / 1.70 / 1.9 | 2.39 / 3.69 / 37.3 |
| `append` | 0.002 to 0.004 | 0.011 to 0.015 |

A diff of 1,200 changed-in-place lines (3,603 unified rows): `typeset.diff`
23.3 ms first and 0.16 steady; the same rows as ImGui lines 2.3 and 2.2. In
DETAIL (`scripts/bench_panes.py`, load 2.4): a deliverable of 970 lines is
19.8 ms first and 4.79 steady on pptmstr's renderer, 35.8 and 0.18 through the
library. **So pptmstr builds nothing over frames any more**: the budget, its
pieces and its pacing are deleted with their tests, and what is left of the
question is the first draw of a very long text, 33 ms a thousand lines, once.

| # | request | the measurement behind it | state |
|---|---|---|---|
| P29 | **A tab in a fenced block survives a copy as a tab.** | By a run (pptmstr's builder, 2026-10-12, `38f5fac`): a tab in a fence is set as spaces to its stop, and copied so. Nothing is absent from the screen, but a Makefile recipe an approver copies out of a body would not run. | open |

### What building the transcript showed (2026-10-12)

By runs through pptmstr's seam on advtext `38f5fac` (pptmstr's builder, an
agent; headless frames for the timings, the application under Xvfb and
llvmpipe for the rest; draw-list counts and the library's own answers, no
pixel read, nobody looked).

The context log is the pane with **many views in one window**. Its markdown
runs (what a model said, and its reasoning) are the library's and every run
between them (a tool call, its result, an error) is ImGui's, by P21. So
closed runs are a `TypesetView` for each markdown run, and the open run a
`TypesetStreamView`: 50 views for the last 400 lines of a session of
three-run turns, 400 for 3,199 lines.

- **A view out of the window costs a call** (`scripts/bench_panes.py --typeset
  on --only views`, four runs, load average 3 to 10): 400 views in one scrolling
  child, about thirty of them in sight, are **11 to 17 ms a frame with
  nothing new**, 0.03 to 0.04 ms a view; the library culls glyphs, and
  `TypesetView.draw` still lays out, draws, sizes, hit-tests and reads keys.
  pptmstr now leaves alone a document that is out of the window and unchanged,
  claiming the height its last draw measured (0.9 to 1.6 ms for the same 400
  views), which costs it two things it would rather not own: the room is an
  estimate where the library's was (a block never in sight), and a selection
  that has scrolled away has to be special-cased so that its keys still work.
  Request P30.
- **Costs in the pane** (`--only transcript watch prune`, CPU ms inside the
  pane's draw, 900 by 700, load average 3 to 10). TYPESET is the library,
  RICH pptmstr's own markdown renderer, WRAP ImGui's wrapped lines:

  | | TYPESET | RICH | WRAP |
  |---|---|---|---|
  | nothing new, 1,080 / 10,080 lines, median | 0.23 / 0.27 | 5.90 / 6.04 | 1.03 / 2.16 |
  | a token a frame, 1,080 / 10,080 lines, median | 1.23 / 1.38 | 6.19 / 6.48 | 1.07 / 2.70 |
  | first frame, 1,080 / 10,080 lines | 71 / 34 (the last 400 lines) | 25 / 227 | 2.5 / 12 |
  | 20,040 lines watched, 16 characters a frame, two runs: median | 1.26, 1.05 | 5.51, 5.85 | 1.94, 2.61 |
  | the same: p95; max | 2.20, 1.76; 62.1, 44.0 | 8.29, 8.39; 47.9, 66.8 | 4.67, 5.41; 12.9, 50.3 |
  | the same: full collections; frames over 16.6 ms of 52,313 | one a run, 56 and 40 ms; 1, 16 | one a run, 43 and 61 ms; 35, 24 | none; 0, 40 |
  | returning to a node whose line cache was dropped, 5,040 lines | 4.0 | 68.5 | 4.8 |

  The two 20,040-line runs overlapped each other and other work on the
  machine, and the count of frames over 16.6 ms follows that more than the
  mode: WRAP's 40 are in a watch with no collection at all. Python's
  collector, which stopped a frame for 109 ms once in every 600 on the old
  core, ran one full collection in each whole watch.
- **P9, sharpened by a run** (`scripts/verify_typeset.py`, a real window):
  `x 😀 y 漢`, two of whose characters no face pptmstr ships has, adds 16
  vertices (four quads, one for each character that is not a space) and
  `TypesetView.stats.whole` is `True`. What the two quads are was not read
  (no pixel, nobody looked). So `whole` does not stand in for the count P9
  asks for: the view reports nothing amiss over text it cannot have set as
  written.
- **P19 in the log**: a run that closes is set again as a settled view, so a
  table's columns move in that frame. Not rebuilt in pptmstr.
- **`advtext.Finder` does not serve the log's search box as it is, and
  pptmstr has not built on it.** Read at `38f5fac`, not run. The box is a
  *filter*: it hides every line that does not match. A `Finder` is a needle
  over one document and marks matches in place. To use it the log would need
  an index for each of its views built from the descriptions that view was
  given (the seam sets a fence's info string as a line of its code, so those
  are not the library's own), new marks for a view whenever its stream is
  read, one current match and one `scroll_to` across views, and something of
  pptmstr's own for the literal runs, which no `Finder` can search or mark
  because the library does not draw them. That is a find-in-text feature and
  new scope; with P21 answered and literal runs handed over as host blocks a
  generation would be one view and most of it would fall away.

| # | request | the measurement behind it | state |
|---|---|---|---|
| P30 | **A view wholly outside the window's clip rectangle costs next to nothing to draw**, and still claims its height. | 400 `TypesetView`s in one scrolling child, about thirty in sight: 11 to 17 ms a frame with nothing new, 0.03 to 0.04 ms for each view out of sight (pptmstr's builder, 2026-10-12, four runs, headless, load average 3 to 10). A log is a view for each markdown run while its literal runs are ImGui's (P21), so views out of sight are most of them. pptmstr skips the draw itself meanwhile (`typeset.document(rests=True)`), at the price above. | open |

| # | request | the behaviour behind it | state |
|---|---|---|---|
| P31 | **A name pptmstr can require**: advtext (and imtypeset, your H3) installable by a requirement string that an installer resolves without a clone at a mutable place — a release artefact per tag with a hash, or an index entry under a name you hold. | `advtext` is unclaimed on PyPI and pptmstr cannot name it in `dependencies`: bare, it is whoever registers it; as a git reference, pip clones again over what a bootstrap installed and an offline or mirrored install fails. A wheel of pptmstr today declares no dependency on the library that sets its text. | open |

P32 and P33 come from a review of the re-adopted panes and the fixes that
followed (pptmstr's builder, 2026-10-12, commit `029cbc8`, advtext `38f5fac`).

| # | request | the measurement behind it | state |
|---|---|---|---|
| P32 | **A reader option for backslash escapes set as written**, and a supported way for a host to mark punctuation it wants taken literally. Belongs with P7. | By a run, settled and streamed: `C:\_work\*.log` was set as `C:_work*.log`, `foo\.py` as `foo.py`, `\\server` as `\server`. An approver reading a path or a regex in a concern must see the backslashes. pptmstr now replaces the reader's `escape` rule in its private reach, and, because its own shaping used a backslash to protect punctuation, marks that punctuation with U+FDD0 instead: an in-band mark it would rather not own. Underscores were checked at the same time and are set as written (`__main__.py`, `__all__`). Not reached: `\|` in a table cell, a backslash in a link's destination. | open |
| P33 | **`reset()` should not need the environment it was first given.** | pptmstr finds its own checkout's library, sets the two variables for its process, asks `typesetter.module()` once, and then removes them, because every agent session it launches would otherwise inherit them and run another worktree's tests on this checkout's library (reproduced: a child process had both). Read, not run: advtext asks the variables again when its "tried" state is reset, so a "try again" (P18) after that would find no library. Either the route taken is remembered across a reset, or a host can hand the pair to `module()` without the environment. | open |

### Trial of the heads beside the pins (2026-10-12, 08:32 to 08:45)

By pptmstr's builder, an agent: advtext `35c6e59` and imtypeset `2fd705e`
(local tag `v0.3.0`) built into a scratch directory from `file://`
repositories, found by the environment variables, **stand-in headers**;
pptmstr's tree at `a28d6e1` unchanged, from a clean export. Xvfb and llvmpipe
for the probes (load 3 to 6), headless for the suites (load 2 to 15).
Positions and counts; nobody looked at a picture. No pin moved. Neither
commit is on GitHub (the lead fetched the branch page on 2026-10-12: its
head is `38f5fac`, and imtypeset's tags page is empty).

- **Monospace columns and hit tests are the same at the heads as at the pins,
  to the digit**, in a fence and in `TypesetDiffView`, at every pixel height
  from 14 to 20: at 16, steps of 7 and 8 px, rows 13 px apart at a column, the
  40th character at 297 against ImGui's 312, a hit test at a drawn glyph
  answering another character for 40 of 64 glyphs of a 74-character diff row.
  At 15, 17 and 19 px the long steps come first and glyphs stand right of
  their rectangles (up to +9.35 px), and nearly every hit test answers a
  neighbour (62, 64 and 61 of 64). **No option for layout from baked advances
  exists in either commit** (H34, H35 in progress at imtypeset, uncommitted).
  So P21 and P22 stand, and pptmstr's diff view stays behind its measured
  check.
- **P23 is met at the head**: the one space laid out at 4/3 width is gone.
- **pptmstr's suite against the head pair: 11 failed**, 3541 passed, 24 strict
  xfails unchanged, none turned. None of the 11 is a regression of yours:
  seven are requests met and held here by a plain assertion that the old
  behaviour holds (P29 a tab is now a tab, five cases; P24 the library now
  tells two ImGui contexts apart; P1 `kind()` exists), and four are pptmstr's
  own test doubles and inference meeting `kind()`, P24 and themes sized from
  the library's `font_metrics` (P25). The application walked 720 frames with
  the typeset branch on in all five panes, exit 0, with identical draw counts.
- **Met by run through pptmstr's own code**: P33 (after pptmstr clears the
  variables, `reset()` then `module()` loads again, "remembered", and a child
  inherits neither); P14 (`typesetter.font` is the same `ImFont` as the atlas
  index for four faces); P30 half (400 views with nothing resting: 11.7 to
  12.9 ms a frame against 21.4 to 24.3 at the pin; pptmstr keeps its resting,
  1.8 to 2.1 ms on both).
- **`ReaderOptions` against pptmstr's reach**: seven of nine sample strings
  agree, settled and a character at a time. The two that do not are P35
  below, and they are what stops the private reach being deleted at the bump.
- **The build**: `v0.3.0`'s script no longer falls back to stand-in headers;
  it fetched `pthom/imgui` from GitHub and failed here (no network). It built
  only with `IMTYPESET_BUNDLE_HEADERS=stand-in`. pptmstr's `make deps` will
  need that said in its environment, or a reachable fork, when the pin moves.

| # | request | the measurement behind it | state |
|---|---|---|---|
| P35 | **Two reader gaps beside `ReaderOptions`**: an entity set as written (`R&amp;D` in the source is five characters on screen, not `R&D`), and a way to mark punctuation literal **inside a markdown string and a stream** (P32's second half; a host-made `MarkdownText` is no answer for a stream or for text that is otherwise markdown). | By trial at `35c6e59`: with all five options on, `R&amp;D` is still set as `R&D`, which pptmstr's probe refuses, and nothing replaces pptmstr's U+FDD0 mark. Also seen: `\|` in a table cell is now set as `\|` where pptmstr's reach sets `|`; a backslash in a link's destination is lost on both. | open |

**The bump, when a commit is on GitHub** (from the trial; run unless marked):
move both ref files together; `make deps` with the headers named; the
library's stamp is now `<library>.attestation.json`; then in pptmstr:
`gate._kind` loses its fallback, `gate.face_font` becomes `typesetter.font`,
`gate._reader` becomes `ReaderOptions` less the two gaps of P35, the context
guard and four test doubles are rewritten, the five tab assertions flip, and
the options that answer P11, P12, P13, P16, P19 are turned on with their
strict xfails expected to turn (read, not run). **pptmstr's lead would make
that move once, at the renamed commit** (below), unless a pushed `advtext`
commit carries the monospace fix first: then the diff view is worth a move of
its own.

### Notice: a baked size can hold another size's glyphs, and it is not yours (2026-10-12)

The user saw letters of one markdown heading in pptmstr's context log drawn at
different sizes (two screenshots, 2026-10-12). pptmstr's builder measured it
(`scripts/measure_heading_glyphs.py`, commit `aa7b25d`; Xvfb and llvmpipe,
`imgui-bundle` 1.92.900, load 4 to 20): every glyph of the heading comes from
one atlas entry at one baked size (`Roboto-Bold.ttf` at 19 px), and 7 of 16
glyphs of that bake hold the bitmap and the advance of the same character at
16 px. **It reproduces with no typesetting library in the path**
(`imgui.push_font` and `text_unformatted`), so it is Dear ImGui's FreeType
loader in the wheel. Trigger as measured: a baked size not drawn for two
frames, the atlas running out of room, then new characters arriving in a bake
that moved. Cause, **from reading upstream v1.92.9, not the wheel's source**:
the loader skips `FT_Activate_Size` when a remembered `ImFontBaked` pointer
equals the current one, while the atlas compacts its baked pool by `memcpy`.
That the user's pictures show this defect is an inference from shape.

**Since (pptmstr commit `d096838`):** the cause no longer rests on reading. A
test with no window and no library builds the sequence from the reading alone
(one discarded size ahead of two adjacent sizes of one font, the first of
them last activated) and gets 16 of 16 later glyphs wrong, each exactly the
other size's glyph; the same when the library's own view loads the glyphs.
Read through a summarising fetch on 2026-10-12, `imgui_freetype.cpp` at
ocornut's `master` and `docking` and at `pthom/imgui`'s `imgui_bundle` branch
still compares the `ImFontBaked` pointer, and no issue was found for it; it
is the user's to file. pptmstr now marks every baked size on the atlas used
before each frame, and lets all of them go at once when the fonts, the UI
size or the framebuffer scale change (87 µs a frame for ten sizes at load
17 to 19): 0 wrong glyphs where there were 7 of 16. One thing that bears on
a host of yours: **a measurement or probe that bakes a size nothing later
draws is what sets the defect up** — pptmstr's own load-time column check did.

| # | request | why | state |
|---|---|---|---|
| P36 | **A notice to carry to imtypeset, not a change asked of either library**: the library draws one face at many sizes and its glyphs come through the same loader, so a typeset glyph can be another size's bitmap with that size's advance, laid out at the right size. If the core checks drawn against laid-out positions (H34, H35) on a long-lived atlas, this is a second source of difference. | Measured as above. pptmstr is guarding it on the host side (every baked size it draws is touched each frame so ImGui discards none: 0 changed glyphs in 6 runs against 7 of 16 without); the reproduction is library-free and is the user's to file upstream. | open |

### Correction to P22's hit-test figure, and the diff view's adoption (2026-10-12)

**The count pptmstr gave for hit tests was an artifact of how it asked.**
"A hit test at a drawn glyph answers another character for 34 to 40 of 64
glyphs" (above, under P22 and both trials, and the 62, 64, 61 of 64 at odd
pixel heights) was taken at the **centre of each glyph's quad**. `hit_test`
answers the nearest position *between* characters, so a centre lands on
either neighbour by the glyph's bearing: at an even em, where every column is
exact, the same method calls 15 to 64 of 64 "wrong". Asked properly (pptmstr's
builder, commit `02dfb06`, `scripts/measure_typeset_diff.py`: a point a
quarter of the baked advance from a glyph's pen must answer the position
before the character, three quarters the one after):

| | px 14 | px 16 | px 18 | px 20 | even em |
|---|---|---|---|---|---|
| glyphs of a 74-character diff row whose hit test is wrong | 55 of 64 | 54 of 64 | 53 of 64 | 53 of 64 | 0 of 64 |

So P22 stands and is worse than first said at pixel heights, and the earlier
numbers should not be quoted. The glyph-against-rectangle distances (up to
16.9 px) were measured differently and are unaffected.

**pptmstr's diff view is wired and waits on a measured fact** (commit
`02dfb06`). DETAIL and the inbox row draw an approval's diff with
`TypesetDiffView`, and a settled document hosts an honest `diff` fence with
`TypesetDiffFences`, **when a check made at font load on the running library
holds**: one 186-character row set as the diff view sets its own, drawn to
buffers, must have (1) the view's `grid.cell` equal to the advance ImGui
baked for the size the glyphs were drawn from, within 1e-4 px, (2) every pen
at its index times that advance, (3) the quarter-point hit tests right. On
`38f5fac` it is false at pixel heights 14 to 20 (155 of 156 glyphs off
column) and true at ems 12 to 22 even, which no setting of pptmstr reaches
(ImGui rounds the UI font to a whole pixel). So today both panes still draw
diffs with their own renderers, and **the day a library that answers H34 and
H35 is installed they change with no change here**. It costs 3 to 6 ms at
load. What the check does not see: it measures a native code block styled as
the view's rows, not the view; a library that set a row differently inside
`TypesetDiffView` would pass it. If H34 arrives as an option a host must turn
on, pptmstr needs to be told its name; the check only reports the result.

### Three requests from closing the diff review (2026-10-12, pptmstr `94e2cf1`)

By pptmstr's builder, runs headless on advtext `38f5fac`, imtypeset
`c9f8902`, load 2 to 9. P37 is live today for an approval body.

| # | request | the measurement behind it | state |
|---|---|---|---|
| P37 | **A view says when what it was given was cut**: `draw()` false, `stats.whole` false, and an answer per view that names what was left out. | A markdown table row of more than 1,024 cells (2.2 KB of a concern's body): the library "left out the rest"; `TypesetView.draw()` answers true, `stats.whole` true, `table_geometry` 1,024 columns of 1,100 written, 152 of 2,200 cells on no screen. The same for a table past 262,144 cells, and for a block or diff row past 16 MiB when the cut is out of view. The only trace is `typesetter.note()`, a process-wide last-message string. pptmstr holds an approve over a body it cannot show, so it now compares that string **by identity** across each call, which works only because the note is built afresh each time. What does make `draw()` false, by a sweep of every code point to U+FFFF and the emoji block in prose, fences, headings and diff rows: nothing but a block over 16 MiB in view. | open |
| P38 | **A lone surrogate in the text is not an exception out of `draw`.** | `json.loads('"\\udc80"')` gives one, so a tool call's argument can carry it: `UnicodeEncodeError` out of `TypesetView` and `TypesetDiffView.draw`, on every frame the call is queued. pptmstr now treats any exception out of a library call as the library failing (logged once, typesetting stopped with a sentence, the pane's own renderer from the next frame), so one such string turns typeset text off for the session. | open |
| P39 | **`code_face_lacks`, or the question behind it, as a supported name.** | A diff row holding a character the code face lacks is set from another face at that face's advance: at an even em, in `x π x Ж x` the third character is 18 px in where the grid says 16 and the fifth 40 where it says 32. pptmstr draws such a diff with its own rows and asks through `typesetter.module().face_has_glyph`. A tab was checked at the same time and lands on the grid. | open |

**Noticed, not asked:** in typeset prose the library sets U+0430 (Cyrillic
"а") as a real letter from Roboto, where pptmstr's chrome font has no
Cyrillic and draws U+FFFD. So a lookalike in a path or URL inside a concern's
body is visible as odd on pptmstr's own renderer and not in typeset text.
pptmstr guards link text already and now keeps such diffs on its own rows;
nothing guards a path in prose. It is a property of setting text well, and is
recorded here because an approval surface is where it matters.

### Coming: advtext is to be renamed `imscribe` (the user, 2026-10-12)

The user, to pptmstr's lead: "Heads up on an upcoming change for the advtext
team: "advtext" is being renamed to "imscribe"". advtext's file says it is to
be one commit on a quiet tree after the diff-view work, with no call's
signature or behaviour changed. **Nothing in pptmstr changes until that
commit is named there and is on GitHub.**

What the rename touches here, counted on 2026-10-12 at pptmstr `a28d6e1`
(`git grep`): 60 import lines in 17 files (four in the seam,
`pptmstr/ui/typeset/{gate,themes,views,diffs}.py`; five scripts; eight test
files); 52 string-keyed uses in the package, scripts, `Makefile`,
`bootstrap.sh` and `pyproject.toml` (the distribution name pip is asked for,
`advtext.ref`, `ADVTEXT_REPO`, `ADVTEXT_PIP_ARGS`, the default repository URL,
`.deps/advtext`, the module names `--version` and the facade test look up);
and this file's own name. It will be one commit of pptmstr's, made with the
pin's move to the renamed commit and not before.

| # | request | why | state |
|---|---|---|---|
| P34 | **The rename is a commit that changes names only, and the commit before it is named too.** And say, when it is specified: whether an `advtext` alias ships for a release, whether the GitHub repository's URL changes, and what the distribution is called to pip. | pptmstr moves its pin in two steps so that a failure can be told apart: first to the last `advtext` commit (behaviour: the diff-view work, reader options, the rest of what was built for it), suite green; then to the rename, where the only edits are names. If behaviour and names arrive in one commit a red suite does not say which. pptmstr's `bootstrap.sh` fetches by repository URL and installs by distribution name, so both are things it has to be told. | open |

## Where pptmstr stands (2026-10-12, at commit `b20dd00`)

- **Built against** imscribe `bb240c3` and imtypeset `2fd705e` (the section
  below), **stand-in headers**; every `make` target and every test run now
  says so in its first lines.
- **Every pane sets its markdown through the library**, and **an approval's
  diff is the library's diff view by default** in DETAIL and the inbox, on a
  whole-pixel cell that pptmstr measures at font load. A diff falls to the
  pane's own rows, with a line saying why, when the measurement fails, a row
  holds a character the code face lacks, the call carries no texts, or the
  diff is past the bound of what is built in a frame (then a button asks for
  the view). Tool output, arguments, errors and a fence inside a document
  are still not on a kept column (P21).
- **What an approve is never decided over** (each held by tests that fail
  without it): text the library cut or set in part; a view whose library call
  raised; a body or argument past 2,000 lines until it is asked for; a pane
  that failed to draw; and, found on the way and older than this branch, a
  tool argument holding a NUL (ImGui drew `ls\x00; rm -rf x` as `ls`) or a
  lone surrogate. Such code units are written out as `\uXXXX` for display
  where session text enters pptmstr; the tool is sent the original. A diff
  holding invisible or bidirectional control characters says so by code point
  on whichever renderer draws it.
- **pptmstr keeps every baked font size alive each frame**, the host-side
  guard for ImGui's mis-sized glyphs (the notice above).
- **The gate**, by `make check` after all writes had stopped: with the pair,
  lint and typecheck clean, 3930 passed, 0 skipped, 23 strict xfails (20
  under P9, P10, P11, P12, P13, P15, P16, P17, P19, P21; 3 that predate this
  work), 2 failed; without the pair 3276 passed, 674 skipped for want of the
  library, 3 xfails, 2 failed. The 2 are `tests/test_launcher.py`'s, from an
  uncommitted line in `pptmstr/model_catalog.py` that is not this session's
  (an export of `HEAD` passes them).
- **The application was run** under Xvfb and llvmpipe, 720 frames: with the
  pair the library's diff view drew in both panes on every frame it was asked
  (360 draws) and no own diff rows; forced off and without the pair, the
  panes' own rows; exit 0 each. The user has six screenshots under
  `shots/bump-b/`; nobody else has looked at a picture.
- **Re-read last** with imscribe's checkout at `a45edd5` (its pushed head is
  `bb240c3`, which is the pin) and imtypeset's at `60b2f32`, which names a
  `v0.4.0` answering H34 and H35; imscribe's `imtypeset.ref` is still
  `2fd705e`, so pptmstr's is too.

**Next, when imscribe consumes imtypeset `v0.4.0`:** move both pins, re-run
`scripts/measure_typeset_advances.py`, and where a fence and prose monospace
hold their columns, let tool output, arguments and errors go through the
library and drop the three P21 xfails. **And independent of that:**
`ReaderOptions` in place of the private parser reach (less P35's two gaps),
and the options for P11, P12, P13 and P19.

**Measured in simulation only, and it bears on the Mac** (pptmstr's builder,
`e6e2065`): with the framebuffer scale patched to 2, pptmstr's column check
says no ("lays out a 8.000 px column where ImGui draws 16 px"). If a real 2x
display does the same, every diff on a Retina Mac is its pane's own rows.
Nobody has run a display that is not 1x. This is P5 and your H19.

## Publication, as pptmstr read it on 2026-10-13

The user: "Both imtypeset and imscribe are finish up and publishing.
imtypeset already exists in PyPI."

- **imtypeset is on PyPI** (the lead read `pypi.org/pypi/imtypeset/json`
  through a summarising fetch): `0.4.1.192900` and `0.4.1.192801`; the former
  requires `imgui-bundle==1.92.900`, is MIT, and has eight wheels uploaded
  2026-10-13: CPython 3.11 to 3.14, each for `macosx_14_0_arm64` and
  `manylinux_2_28_x86_64`. No file or version says `standin` or `dev`; which
  headers the wheels were built with cannot be read from the listing.
- **imscribe is not on PyPI yet** (404 at the same time). Its repository is
  renamed `WalterCReel3/imscribe`; the branch head on GitHub is still
  `bb240c3`, pptmstr's pin. Its move to imtypeset `0.4.1` and to version
  `0.2.0b1`, with `imtypeset==0.4.1.192900` as a requirement, was in its
  working tree and not a commit when the lead looked.
- **So pptmstr has nothing new to build against yet.** When imscribe names a
  pushed commit or a published version on imtypeset 0.4.1, pptmstr: takes
  the three as exact requirements in `pyproject.toml` (the reason imscribe
  left them, an unclaimed name, is then gone: P31); installs from the index
  in `bootstrap.sh` and keeps the build from source as the development
  override; finds the library as an installed distribution; points its
  default repository URL at the new name; and re-runs
  `scripts/measure_typeset_advances.py`, since 0.4 is the core's answer to
  H34 and H35, to see whether a fence and prose monospace now keep their
  columns (P21).
- **Not testable from this session:** anything that installs from PyPI. The
  first install of the published pair into a fresh environment, on Linux and
  on a Mac, is the user's to run; `python -m pptmstr --version` is the line
  to read.

## The published pair, run by the user (2026-10-13)

Both are on PyPI: `imscribe` 0.2.0b1 (read from PyPI's JSON by the lead:
requires `imgui-bundle==1.92.900` and, under a marker, `imtypeset==0.4.1.192900`).
**The user ran it**, on Linux x86-64, CPython 3.11, in a fresh venv made by
`pip install imscribe==0.2.0b1`: `python -m imscribe.typesetter` exited 0 and
printed `"kind": "ready"`, `"route": "distribution"`,
`"imtypeset_route": "installed"`, `"distribution": "imtypeset 0.4.1.192900"`,
`"headers": "fork:c9a0a84f8a4ddae794a9e1acf6a93afce73d5057"`,
`"accepted_by": "imgui-check+probe"`, `"ignored_environment": null`. So the
published wheel is a fork-header build that Dear ImGui's own check accepts:
the first such library pptmstr will have run on. The user also put the two
published files in this worktree (`.wheelhouse/`, untracked), so pptmstr's
move to the published packages is tested here against those files and not
against a source build. Not yet run by anyone pptmstr knows of: the same on
macOS.

## pptmstr requires the published packages (2026-10-13, pptmstr `40e8586`)

`pyproject.toml` now names `imgui-bundle==1.92.900`, `imscribe==0.2.0b1` and
`imtypeset==0.4.1.192900` (under imscribe's marker, character for character);
tests hold the three to each other and to what the installed imscribe
requires. `bootstrap.sh` installs from the index and **has not been run by
anyone**; the build from source (`make deps`, the ref files: `imscribe.ref`
`b98de08`, `imtypeset.ref` `e6160c0`) is the development override.

**What was exercised here, and with whose install** (pptmstr's builder):
imscribe is the user's downloaded wheel, installed in this worktree's venv
and held file for file to the tag's commit. imtypeset is the published wheel
**as installed in the imscribe checkout's venv**, reached through this
worktree's composite path; its cached download is byte for byte the file the
user downloaded. The same wheel installed into this worktree's own venv is
`refused`, `imtypeset_kind` `layout` ("imtypeset and imgui-bundle are
installed in two different places…"), because this venv borrows its
`imgui_bundle`. That is this venv's fault and the wheel's rule working.

**How pptmstr finds the library**, first that applies, and `--version` says
which: a variable the caller set (ignored beside an installed imtypeset, and
said so); `IMSCRIBE_NO_TYPESET=1` (none; pptmstr hands over nothing behind
it); an installed imtypeset; this checkout's `.deps`, only where no
distribution is installed or `PPTMSTR_TYPESET_DEPS=1` asks (pptmstr then puts
the built package first on the path, because the two variables alone are
ignored there: its own choice, where your documented way is to uninstall the
wheel).

**Gate at `40e8586`**, by `make check`: with the installed library (fork
headers) 3936 passed, 0 skipped, 23 strict xfails, 2 failed (the two not
ours); `IMSCRIBE_NO_TYPESET=1` 3305 passed, 651 skipped, 3 xfails, 2 failed.

### Answers to A6 and A7

**A6, done** (`70f2c9b`): no `cell` is passed anywhere; a test scans for the
keyword. pptmstr's own check, with pptmstr's fonts, holds at every pixel
height from 12 to 24 with nothing passed: 0 of 156 glyphs off their column,
0 mishit. The glyphs are now the chrome's own (at 16: em 15.253 asked and
drawn, raster 16, a `0` of 7×9 in the view and in the chrome; pitch 18). The
17 px raster is gone and so is pptmstr's sentence about it. The measured cell
is 8.000009 at 16; pptmstr answers for 54,418 columns there and 30,474 at 20.

**A7: it matters, and pptmstr is not blocked by it.** A diff is what an
approver signs, and its last column has to be reachable. Measured here at
16 px in the diff view: `xπxЖx` has glyph lefts 0, 8, 24, 32, 48;
`content_width` is 152 px for `x π x Ж x`, whose text ends at cell 11; ten
`Ж` claim 160 px for 20 cells of text. pptmstr sends any diff with a
character the code face lacks to its pane's own rows, with a line saying so,
and will go on doing that until the view counts the cells it draws. Then
such diffs can be the library's too. The same question for a code block in a
document is being measured now, because tool output may hold any script.

**Closed by this move:** P21 and P22 for a fenced row (158 of 158 baked
steps at 12 to 24, 0 px between rows, pens on ImGui's; one strict xfail
turned and is a plain pass), P31 (a name pptmstr can require), P4. **P23**
further: under an all-mono page word spaces are one value at every height.

| # | request | the measurement behind it | state |
|---|---|---|---|
| P42 | **After every baked size is let go, a new document's first layout uses the code face's size from the layout before it.** | pptmstr's builder, `70f2c9b`, a strict xfail in `tests/test_baked_sizes.py`; reproduces with imscribe's view alone: discard every baked size (pptmstr does this when the fonts, the UI size or the density change, as its guard against ImGui's mis-sized glyphs), make a new document: its first layout re-makes one size from the previous layout. One stale size on the atlas. Bears on H48. | open |
| P43 | **`reason()` with no library says which thing is missing.** | It reads `IMTYPESET_LIBRARY is not set` when the wheel is absent and under `IMSCRIBE_NO_TYPESET=1`, where no variable is the remedy. pptmstr words its own sentence from `kind()` meanwhile. You list it as known and open. | open |

## Literal text is the library's where its columns hold (2026-10-13, PYPI-B)

**Measured first, with pptmstr's fonts and role themes** (pptmstr's builder,
2026-10-13 22:53, `scripts/measure_typeset_advances.py --pptmstr`: chrome
Inconsolata with Font Awesome as `Fonts[0]` and the library's faces by
`pptmstr.theme.load_fonts()`, documents themed by `roles.configure` and
`themes.document_theme()`; imscribe 0.2.0b1 on the imtypeset 0.4.1.192900
wheel, fork headers; Xvfb and llvmpipe, density 1, load average 1.6; vertices
read, no pixel, nobody looked). 56 pages: prose in Roboto or in the code face,
at each body size the settings offer (14, 16, 18, 20) and each step from -2
to +4 of the code-block, literal and inline-code roles together, so code at
every pixel height from 12 to 24. The chrome beside each is pptmstr's UI font
at the role's pixel height, from the row's first pen. Beside the table at
the top of A2 (the old core, which held none of R2, R4 and R5):

| at every one of the 56 pages | R2: steps equal to the chrome's baked advance | R4: rows apart at one column | R5: pen less chrome's at characters 1, 10, 20, 30, 40, 74 | glyph against its rectangle | R6: fractional pens | hit a quarter and three quarters into a glyph |
|---|---|---|---|---|---|---|
| a fence in a Roboto document, at the code-block role's size | 158 of 158 | 0 px | +0 at each | 0.00 px | 0 | 174 of 180 (below) |
| a fence in an all-monospace document | 158 of 158 | 0 px | +0 | 0.00 px | 0 | 174 of 180 |
| prose rows in the code face | 93 of 93 at the body's size; every word space one cell | | | | | |
| a host's `MarkdownCode` in the literal role's style, `code_overflow="grow"`, `fixed_line_height=True` | 162 of 162 | 0 px | +0 | 0.00 px | 0 | 181 of 188; tabs at columns 4, 8, 12, 20; a selection's fill on the pens of its first character and the one after its last, +0 and +0 |
| inline code in Roboto prose | whole-pixel steps, from the **paragraph's** em: sixteen characters 112 px at a body of 16, the chrome's 128 (P11: the role's size does not reach a span) | | | | | |

The raster drawn from is the role's pixel height for the fence and the host
block at every page. **The misses in the hit test are each line's last
character**: three quarters into it answers the start of the next line, one
position too far, where the row of `gate.columns` (one line) has no next
line to answer. P44 below.

**A character the code face lacks, in a code block**: `xπxЖx` at 16 px has
its glyphs at 0, 8, 24, 32, 48 (two cells a Greek or Cyrillic letter), ten
`Ж` reach 158 px of ink, the lines 160, and `content_width` is 160. **A code
block claims the cells it drew**, so its end can be scrolled to; A7's defect
is the diff view's and not the code block's. CJK and an emoji take one cell.

**Also measured, of a host's code block** (pptmstr's builder, the same
session, headless): every character is in `texts` and in a copy; ESC, BEL,
BS, DEL, U+200B and U+202E draw nothing, a lone carriage return is a line
break, and a line break at the end is neither drawn nor copied.

**What moved, behind a measurement of the running library**
(`gate.literal_columns`: the same questions as `gate.columns`, asked through
`gate.literal_view` and the block literal text is drawn with): a literal part
whose caller asks (`document(literal=True)`, `plain(literal=True)`) and that
holds none of the characters above, does not end in a line break, is not
wrapped, and has no line past the measured reach, is one `TypesetView` of one
`MarkdownCode`. The callers are **the context log's tool calls, results and
errors** (closed runs and the open one) **and DETAIL's error**. Its line is
the role's pixel height, as ImGui's is, **but one pixel more at 14, 18 and 22
px**: under `fixed_line_height` the library puts a baseline three quarters
down the line, so a line of 4k + 2 px is a baseline on a half pixel, and
which way each row is rounded then depends on where the block is on the
screen. Measured (eight rows at 60 heights down a window, every pixel
height 12 to 24, lines of that height and one and two more): 49 of 60
placements put a row a pixel off at every 4k + 2 line and none at any
other. P45 below. Not moved:
everything wrapped, single-line errors, the inbox, and **an approval's
arguments** in DETAIL and the inbox (below).

**An approval's arguments stay ImGui's** (an agent decision). Shown on the
literal path: a cut and a raise under a literal view report every line unset
through `ViewCache.unbuilt`, which is what the approve holds read
(`tests/test_typeset_literal.py`). Not shown: any of the approve holds
themselves (`review.withheld`, the 2,000-line bound, a pane that failed to
draw) over arguments set this way, since nothing in DETAIL or the inbox sets
them this way; that is the next task's to build and prove, pane by pane. A
hidden or bidirectional character keeps a part ImGui's (`views.literal_unfit`).
A prose argument's `prose_parts` gives literal parts of `Role.LITERAL`, so the
opt-in is per call and not per role, and the same test file reads which
functions pass it.

**Costs** (`scripts/bench_panes.py --typeset on --only literal`, headless,
300 frames, four runs at load averages from 1.5 to 7.3, first frames within 6 % of each other): DETAIL's error
of 300 lines 5.5 ms first and 0.12 steady, ImGui 0.27 and 0.07 (an error is
cut at 20,000 characters, so 5,000 lines is the same); the log's tool result
of 300 lines 6.6 / 0.19 ms, ImGui 1.3 / 0.13; of 5,000 lines under the log's
bound (399 held) 24 / 0.19, ImGui 3.8 / 0.13; with every line held 95 to
100 / 0.19, ImGui 5.2 / 0.16.

| # | request | the measurement behind it | state |
|---|---|---|---|
| P45 | **Under `fixed_line_height`, every row of a block on its line wherever the block is drawn.** | Above: a line of 14, 18 or 22 px puts the baseline at 10.5, 13.5 or 16.5 px into the line, and one row of eight is drawn a pixel off its line at 49 of 60 positions down a window (none at a line of any other height). pptmstr sets such lines a pixel taller meanwhile (`themes.literal_line`). | open |
| P44 | **A point past the middle of a line's last character is answered as the end of that line, not the start of the next.** | Above: in a code block of seven lines, three quarters into the last character of each of the first six answers position k+2. A press there puts the caret on the next line and a drag to the end of a line takes its line break. | open |

## The pins moved, and the diff view is live (2026-10-12, pptmstr `8c6a43e`)

The file keeps its name; the library is `imscribe` from here on.

| | |
|---|---|
| imscribe | `bb240c3db97aaef4c62382d3822913f70acfa540` (`imscribe.ref`), the head of `experimental/imtypeset-adoption` on GitHub as the lead read its commits page on 2026-10-12; no package code differs from the rename `cd437c1` |
| imtypeset | `2fd705e2bc358c7dbfda1eb60bd9b71609869e1d` (`imtypeset.ref`), what that commit's own `imtypeset.ref` names; on GitHub's `main` |
| how built | `make deps` from `file://` repositories; **stand-in headers by an explicit opt-in** (`IMTYPESET_BUNDLE_HEADERS=stand-in`). pptmstr's script asks for the fork's headers by default, and that default was run here once and failed cleanly for want of network; **a fork-header build has never been made by pptmstr** |
| the move | in the two steps of P34: to `68eb18b` (`a945ed2`, `4401659`), the diff view turned on there (`da75e3d`), then across the rename (`8c6a43e`); the suite was green at each, and the rename changed four tests' count only by the four added for removing the old distribution |

**The diff view** (`da75e3d`): `cell="pixel"` in the one function that makes
both the view pptmstr draws and the view its check measures, and in
`TypesetDiffFences`. Measured in pptmstr's window with pptmstr's fonts (chrome
Inconsolata with Font Awesome, the vendored bold registered; Xvfb, vertices,
load 3.8): the check holds at every pixel height from 12 to 24; one step on
every row, 0 px between rows at a column, pens on ImGui's at characters 10,
20, 40 and 74, 0 of 64 quarter-point hit tests wrong, every glyph 0.00 px
from its rectangle, tabs on the grid. With the default cell it holds at none.
So with nothing forced, DETAIL and the inbox draw an approval's diff with
`TypesetDiffView`, and a settled document hosts an honest `diff` fence. Cost
as measured here: at even heights the em grows 4.9 % on the raster one pixel
larger (at 16: a `0` of 8×11 against the chrome's 7×9, pitch 18); no printable
ASCII glyph crosses its row at 12 to 24; U+2502 by 2 to 3 px. First frame 4 to
10 ms, steady 0.35 ms, at 300 and 1,200 lines (load 7.3). The user has
screenshots to look at; nobody else has looked.

**Requests closed by this move** (built against, in pptmstr's own code and
tests): P1 (`kind()` is what pptmstr's gate reads; its own inference is
gone), P14 (`typesetter.font`), P20, P23 (the wide space is gone from the
probe), P24 (pptmstr's own context guard is removed), P29 (a tab is a tab; ten
assertions flipped), P33 (after pptmstr clears the variables, `reset()` then
`module()` loads again and a child inherits neither: a test through the entry
point), P34, P38 (a lone surrogate is set as U+FFFD and nothing is raised),
and **P21 and P22 for the diff view only**.

**Still open, with what pptmstr does meanwhile:** P21 and P22 for a fence in
a document and every other monospace role (three strict xfails; tool output,
arguments and errors stay ImGui's; waits on imscribe consuming imtypeset
`5077b22`). P7, P32, P35 (the private parser reach stays until `ReaderOptions`
is adopted, next). P11, P12, P13, P19 (options exist, not yet turned on here;
strict xfails). P16 (`headers()` exists; pptmstr still says a header above the
view always, because `headers()` does not cover a hunk's section row and does
not know the width under a height cap before the draw). P2 (`verdict()` not
used: `--version` asks `module()` in a process that exits). P25 (themes are
sized from the library now; the cost was not re-measured). P9, P10, P15, P17,
P26, P27, P28, P30 (pptmstr keeps resting off-screen documents: 9.2 ms with
nothing resting against 1.3 ms, 400 views, load 3), P31, P36, P37, P39.

| # | request | the measurement behind it | state |
|---|---|---|---|
| P40 | **Under `cell="pixel"`, `grid.cell` says the whole pixel while the row is laid out a little off it**, or the layout is exact. | By a run (pptmstr's builder, `da75e3d`): the library accepts a layout within 1e-3 px of the pixel and reports the pixel; the real layout is 1.3e-6 to 2.4e-5 px a column off and accumulates, 1 px at column 100,000 and 11 px at 1,000,000. pptmstr's check had trusted `grid.cell` and answered for a million columns; it now also reads the measured row's own width and caps adoption near 45,000 columns at 16 px. A host that trusts `grid.cell` for a minified one-line file is off by a pixel or more at its far end. | open |
| P41 | **A lone surrogate set as U+FFFD is said somewhere a host can read** (P38's other half). | At this pin the library sets it as U+FFFD with no exception and no note, so an approver reads a replacement character where the argument that will be sent holds something else, and nothing tells the host. pptmstr is escaping such characters for display before they reach any renderer; the request is for the view to say when it substituted. Belongs with P9. | open |

### A4 — every imscribe name pptmstr imports (at `8c6a43e`, by an AST scan)

- In `pptmstr/` (all in `pptmstr/ui/typeset/`, all `from imscribe import`):
  `Align`, `BlockKind`, `Diff`, `DiffLayout`, `DiffPalette`, `Face`, `Faces`,
  `FileStatus`, `MarkdownStream`, `Palette`, `Spacing`, `Theme`,
  `TypesetDiffFences`, `TypesetDiffView`, `TypesetStreamView`,
  `TypesetUnavailable`, `TypesetView`, `em_size_for_pixel_height`,
  `MarkdownCode` (from PYPI-B, 2026-10-13),
  `parse_markdown`, `px_from_sp`, `register_font`, `registered_fonts`,
  `sp_from_px`, `unregister_font`, and the modules `typesetter` and
  `markdown_rules`.
- Of `typesetter`: `module`, `kind`, `Kind`, `reason`, `ready`,
  `why_not_ready`, `load_fonts`, `unload`, `trouble`, `font`, `reset`,
  `base_style`, `FACE_ROLES`, and `module().face_has_glyph` (P39) and the
  native module's documents for the columns check.
- **Private:** `markdown_rules._parser` (P7, P32, P35).
- Scripts and tests add: `DARK_PALETTE`, `Emphasis`, `RowKind`, `fontlib`,
  `fontlib_metrics`, `imscribe.description.FACE_ROLES`,
  `imscribe.style.fontlib_metrics`.

The A4 section higher in this file is the list as it stood on `38f5fac`.

## Agent decisions, awaiting the user

- `imgui-bundle` is pinned `==1.92.900` in pptmstr (A1).
- pptmstr builds against advtext `38f5fac`, the last commit known to be on
  the remote branch, not against the four unpushed commits after it.
- This worktree keeps its composite `.venv` (a real one is blocked on
  network); the pinned advtext clone replaces the live checkout on its path.
- pptmstr keeps every one of its own renderers through this adoption (P6).
- Monospace roles are not moved to an even em to buy columns (the trial under
  P21): it would set code larger than the chrome, against the sizes the
  operator configured. Offered to the user as an option, not taken.
- The context log still starts in RICH although TYPESET now meets the cost
  rule recorded for making it the default (the table under "What building the
  transcript showed"): nobody has looked at the library's text in that pane,
  and a fence inside a markdown run does not keep its columns (P21). One word
  in `transcript_pane.DEFAULT_MODE`.
- `scripts/profile_typeset.py` and `scripts/compare_metrics.py` are deleted:
  both measured the old core's Python layout and its four metric routes, and
  neither imports. `scripts/verify_typeset.py` and `scripts/survey_fonts.py`
  are repaired onto the new layer.
