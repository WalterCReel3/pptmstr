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

This table is updated at each boundary of the work; the git history of this
file is the record of when the refs moved.

## State of the branch on 2026-10-11, by running (the lead; machine otherwise idle)

- The environment imports. `.venv` holds advtext **editable from the advtext
  lead's live checkout** (`~/Source/imgui-adv-text`, then at `f34f6c8` with a
  dirty tree), which is what `bootstrap.sh` installs. `import pptmstr.ui.typeset`
  succeeds; `advtext.typesetter.ready()` is `False`, `reason()` is
  `IMTYPESET_LIBRARY is not set`.
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
  `bootstrap.sh` makes `.venv` and installs editable; `make run`. There is a
  hatchling wheel target and a `pptmstr` console script, never published.
  No freezer, no signed bundle, no updater, no CI (`.github/` does not
  exist). One thing a freezer will meet that advtext should know of:
  `claude-agent-sdk` carries its own Claude Code CLI executable inside the
  wheel, so the packaged application already has to ship and sign a second
  native binary besides the sidecar.

### A2 — hinting, advances and the atlas, as requirements

**Standing of what follows.** Restated by pptmstr's reviewer on 2026-10-11
**from reading**; nothing was re-run, and nothing below can be re-run today:
`scripts/compare_metrics.py` imports `draw` from advtext (gone), the
`pen_rules.py --hinting` instrument exists only inside
`planning/patches/advtext-hinting.patch` (against the deleted core), and the
pins in `tests/test_typeset.py` are among the errors above. All figures are on
`imgui-bundle` 1.92.900, FreeType 2.13.3, density 1, no GPU.

- **(G)** `shots/metrics/gaps.txt`: output of `scripts/compare_metrics.py`, by
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

**Unknown:** R1 to R6 on the new core in pptmstr (pptmstr is measuring: see
"Work in hand"); whether a fenced code row is one word under the width bound;
anything at density 2, on a GPU, or on a Mac. The operator has not been shown
the new core's text.

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

Kept current here as the adoption lands. Today the seam uses names that no
longer exist; the list starts when the first pane is re-adopted.

### A5 — what pptmstr needs that is not offered

As `P` requests, below.

## Requests of advtext

None yet. Numbered `P1`, `P2`, … as they arise; never renumbered.

## Agent decisions, awaiting the user

- `imgui-bundle` is pinned `==1.92.900` in pptmstr (A1).
- pptmstr builds against advtext `38f5fac`, the last commit known to be on
  the remote branch, not against the four unpushed commits after it.
