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

In progress; the record is `planning/2026-10-08-an-advance-belongs-to-the-rasteriser.md`
and commit `a0ce752` ("pptmstr keeps the hinted atlas, by the operator's
eye"). To be restated here as measurable requirements, not as the patch.

### A3 — fonts

In progress; the record is `planning/2026-10-07-which-fonts-ship.md` and
`planning/2026-10-07-font-survey.md`.

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
