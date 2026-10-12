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
| P17 | **What `typesetter.load_fonts()` promises when called after start-up**, between frames. | The operator turns typeset text on, and changes the proportional family, from the settings dialog while sessions are open. Read: it remakes the library's context and closes every document, and its docstring places it in `load_additional_fonts`. Whether a later call is safe is stated nowhere pptmstr read. | open |
| P18 | **A supported way back from `typesetter.disable()`**. | One provoked failure switches the library off for the rest of a test process; only the private `_forget()` undoes it. An operator-facing "try again" needs the same call. | open |
| P19 | **A settled table at its natural widths**, as a stream holds it. | A table changes every column's width on the frame its stream is replaced by the settled view. Test: `test_a_table_that_fits_keeps_its_natural_widths_settled_as_it_had_streamed`. | open |

## Work in hand

- **Dependencies by ref** (`DEPS` on pptmstr's board): advtext and imtypeset
  fetched at the commits above, the sidecar built inside this worktree.
- **R1 to R6 measured on the new core** (`MEASURE-ADV`): a probe in a
  pptmstr-shaped window, reading the vertex buffer. The numbers go under A2
  when they exist; any request that follows from them is a `P` then, not
  before.
- **The seam** (`pptmstr/ui/typeset.py`) re-adopted pane by pane.

## Agent decisions, awaiting the user

- `imgui-bundle` is pinned `==1.92.900` in pptmstr (A1).
- pptmstr builds against advtext `38f5fac`, the last commit known to be on
  the remote branch, not against the four unpushed commits after it.
- This worktree keeps its composite `.venv` (a real one is blocked on
  network); the pinned advtext clone replaces the live checkout on its path.
- pptmstr keeps every one of its own renderers through this adoption (P6).
