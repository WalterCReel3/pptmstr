# A Mac install is a lock before it is a bundle

**Dated:** 2026-10-11 ·
**Status:** a recommendation; §5's first step is built (2026-10-12), and the machinery for its lock (2026-10-14, §7) with no lock yet written; the move to imscribe 0.2.0 on imtypeset 0.4.3 trialled and written as a checklist, not made (2026-10-15, §8), and the lock is to be resolved after it. **From reading, by pptmstr's
reviewer; nothing was run and nobody had a Mac.** Web pages were read through a
summarising fetch. Apple's developer pages returned only their titles, so every
statement about dyld, Apple's linker, signing and library validation is
recollection and is marked *inference* ·
**Origin:** the user, 2026-10-11 — *"The goal now is initial stability and
deliverability for a full pptmstr adoption and product packaging (goal of a Mac
supported install with automatic updates)."* and *"Let's target Level 1."* ·
**Read with:** `HANDOFF-advtext.md` (requests P1 to P6 come from here), and in
advtext's repository `HANDOFF-imtypeset.md`, requests H1, H3, H5, H6, H15 to H20.

---

## 1. What every shape shares

- **Which Macs is decided by one wheel.** PyPI's JSON for `imgui-bundle`
  1.92.900 lists four macOS wheels, all `macosx_14_0_arm64`, CPython 3.11 to
  3.14; no x86-64, no universal2. `claude-agent-sdk` ships arm64 and x86-64
  from macOS 11. So: Apple Silicon, macOS 14 or later.
- **The compatible unit is four things**: pptmstr, an advtext commit, the
  imtypeset library with its Python package from one commit, and one exact
  `imgui-bundle` wheel binary. An update replaces all four or none.
- **Whether the sidecar works on macOS at all is open (H1).** Reasons it may
  not, all inference: whether the wheel's macOS module exports its ImGui C++
  symbols is a property of imgui-bundle's build; Apple's linker will not link
  against an `MH_BUNDLE`; `-undefined dynamic_lookup` interacts with chained
  fixups on recent targets.
- **`claude-agent-sdk` carries a 251 MB Claude Code executable in its wheel**
  (`claude_agent_sdk/_bundled/claude`; read from the installed 0.2.164's
  `RECORD`), found relative to the package's `__file__`.

## 2. The shapes

| | (a) frozen `.app` + Sparkle | (b) installed interpreter from a lock | (c) Homebrew |
|---|---|---|---|
| what it is | PyInstaller or Briefcase bundle; Sparkle replaces the bundle | a venv made from one lock per release (`uv tool install` or pptmstr's own bootstrap); the updater builds the next venv beside the current one and switches | a formula is (b) in brew's dialect; a cask is (a) |
| the sidecar (H1) | needs H1, and adds an unknown: the bundle relocates binaries, and the check that a second copy of the wheel's module was not loaded reads `/proc` (H18) | needs H1; no relocation | no better than what it wraps; a from-source formula rebuilds `imgui-bundle`, which the gate would refuse |
| the import gate as it is (H15) | **does not work**: `sys.executable` is the bootloader, so the gate's child is the application relaunched with `-P -B -c`; the stamp is written inside a signed bundle | **works as written**: a real interpreter, a writable directory | as wrapped |
| signing | Developer ID, hardened runtime, notarisation of about 400 MB; the sidecar and Anthropic's executable both re-signed by us (*inference*) | none to exist (*inference*: files fetched by an installer carry no quarantine attribute) | as wrapped |
| fonts (H20) | `imgui_bundle/assets` collected explicitly | on disk already | as wrapped |
| partial update | impossible inside the bundle | possible, and the normal case under loose pins; cured by exact pins with hashes applied all-or-nothing | as wrapped |
| what it costs | a macOS arm64 build machine, an Apple account, appcast hosting, a Cocoa-side or out-of-process updater (`sparkle-cli`) | a terminal-shaped install; no Dock icon without a thin launcher | a second packaging dialect, no dependency removed |
| waits on | H1, H3, H15, H16, H18, H20 | H1, H3, H5, H16 **for typeset text; on none of them to exist** | — |

## 3. Recommendation (the lead's, an agent decision awaiting the user)

**Build (b) first, and treat (a) as a later wrapper round the same lock.**

- It is the only shape that does not wait on H15, and it ships on macOS with
  pptmstr's own renderers whatever H1's answer is.
- Everything it needs is needed by (a) too: exact pins, one release manifest,
  a version line that reports the pairing, a refusal shown as a sentence, a
  staged switch with rollback.
- **2026-10-14, the user:** *"Let's write the release lock now."* The lock this
  shape is made from is decided and its machinery built (§7); the lock itself is
  resolved by the user on a networked machine, because the machine the agents
  run on reaches no index.
- Its cost is real and is the user's to weigh: it is a terminal install. If
  "a Mac supported install" means a double-clickable application, (b) is the
  step before it and not the destination. **Not asked of the user; recorded
  here as open.**

## 4. What pptmstr must do whatever the shape

| | today (read) | needed |
|---|---|---|
| pins | `imgui-bundle==1.92.900`, `imscribe==0.2.0b1`, `imtypeset==0.4.1.192900` in pyproject.toml; `imscribe.ref` and `imtypeset.ref` hold the two commits, and what is built under `.deps` is held to them | one lock per release naming all four, with hashes. **2026-10-14:** `make lock` writes it (two files, §7) and `make lock-check` holds it to pyproject.toml offline; bootstrap installs from it with `--require-hashes`. Not yet written: that needs the network (§7.6). pptmstr itself is the fourth part and is the checkout, installed `--no-deps` after the lock |
| version | one source (`pptmstr.__version__`); `pptmstr --version` prints it with the pairing actually running and the reason when not drawing, without a window (commit `9e47b5a`) | the same line from an updater's staging check, which needs advtext's `verdict()` (P2) |
| a refused sidecar | every pane falls back to its own renderer and the Text menu and settings dialog say one sentence; the kind is advtext's where it has one | the kind for "not supported on this platform" (P1), once advtext with `kind()` is the pin |
| own renderers | present for every pane | **kept**, at least until H1 is answered: they are the macOS product if the answer is no (P6) |
| settings | `$XDG_CONFIG_HOME/pptmstr` or `~/.config/pptmstr`; hello_imgui's ini in its own user-config folder | works on macOS as it is; moving to `~/Library/Application Support` later needs a migration, so decide before the first Mac user |
| assets | found by `__file__` (`theme._ASSETS`, `splash_art`, the wheel's fonts, the SDK's executable) | "files on disk at package-relative paths", stated to any freezer; `importlib.resources` buys nothing since hello_imgui and the gate want paths |
| credentials | `model_catalog.read_credential()` reads `~/.claude/.credentials.json` only | on macOS Claude Code keeps its login in the Keychain, so the launcher's model list would be the fallback list with "no usable credential": a degrade independent of advtext |
| sandbox wording | messages name `bubblewrap` and `socat` | macOS uses Seatbelt and needs neither |
| Retina | chrome fonts go through hello_imgui's DPI handling; the library's faces do not (P5, H19) | a person at a Mac |

## 5. First step, buildable and testable on Linux

1. Exact pins and the release manifest (`DEPS`).
2. `pptmstr --version`: version from one source, the pairing, the reason.
3. A test that an environment whose `imgui-bundle` differs from the manifest is
   reported as a sentence.

4. **2026-10-14:** the release lock, decided and built in §7 except for the one
   resolve, which needs the network.

Nothing after that on the macOS side can be verified from this machine.

## 6. Sources

PyPI JSON for `imgui-bundle` 1.92.900 and `claude-agent-sdk` 0.2.165;
pyinstaller.org (run-time information, feature notes, usage);
docs.astral.sh/uv (tools); docs.brew.sh (language-specific formulae);
sparkle-project.org (documentation, programmatic setup, sparkle-cli);
briefcase.beeware.org (macOS); code.claude.com/docs (authentication,
sandboxing). py2app and Nuitka were not read.

## 7. The release lock (2026-10-14)

**Origin:** the user, 2026-10-14: *"Let's write the release lock now."* **Why
now:** within a week a fresh resolve from pptmstr's floors crossed `mcp` 1 to 2
and `anthropic`'s HTTP library (commit `167a3da`); pptmstr survived the first
only because the SDK handles both majors. The set to lock is the one the user's
fresh clone ran green (4071 passed, 8 skipped, 23 xfailed). **Decided by
pptmstr's builder (agent decisions), from uv's documentation read through a
summarising fetch on 2026-10-14; no uv was run, because the machine has none and
reaches no index.** Everything below that says what uv does is from that reading.

### 7.1 Tool and format: uv, writing requirements files with hashes

- **uv `pip compile --universal`**, because one resolve gives one file for every
  target, with markers where versions fork. pip-tools resolves for the machine it
  runs on, so two targets would mean two machines. uv's `--python-platform` is a
  per-target resolve too ("the provided `--python-version` is the exact python
  version to use, not a lower bound"), which would mean eight files for two
  platforms and four Pythons.
- **Restricted to the targets by `[tool.uv]` in pyproject.toml:**
  `environments` (linux x86_64 and darwin arm64, each with `python_version >=
  '3.11' and < '3.15'`), which "will also be respected when `uv pip compile` is
  invoked with the `--universal` flag", and `required-environments` (the same two
  platforms), which fails the resolve where a package has no wheel for one, and
  "is respected by `uv lock` and `uv pip compile --universal`". In pyproject.toml
  and not a uv.toml because the docs give both only as `[tool.uv]` project settings.
- **`--only-binary :all:`** at the resolve and at the install, because bootstrap's
  claim is that nothing is compiled: a package with no wheel fails `make lock` by
  name instead of building at install.
- **The output is a requirements file with `--hash=sha256:` lines**, so the install
  side is plain pip (`--require-hashes`) or `uv pip install --require-hashes`;
  neither needs uv to have made the file. Not `uv lock`/`uv.lock` with `uv export`:
  that is a second file format to commit and keep in step, for the same output.
  Not `pylock.toml` (PEP 751, which uv can write): it was not checked that the
  pip a venv's ensurepip brings installs one.
- **Not decided by a marker, and so not in the lock's guarantee:** glibc 2.28 and
  macOS 14. uv's `minimum-libc-version` is the documented way to hold the resolve
  to a glibc, and it is a *preview* feature, so it is not used. `make lock` sets
  `MACOSX_DEPLOYMENT_TARGET=14.0` because uv's documented default for a macOS
  target is 13.0; the docs say that for `--python-platform`, and whether the
  universal resolve reads it was not established. The probe refuses both floors
  before a venv is made, and the CI job in §7.5 on a glibc 2.28 image is what
  would show a wheel the lock picked needs more.

### 7.2 Two locks, layered

- `requirements-dev.lock`: pyproject's dependencies, the `dev` extra, and
  `hatchling` with `editables` (the build backend, and what hatchling 1.32.4's
  `get_requires_for_build_editable` asks for), because bootstrap installs pptmstr
  with `--no-build-isolation` so its build backend is hashed as well. What a
  checkout installs.
- `requirements.lock`: the dependencies alone, resolved with the dev lock's
  versions as constraints (hashes stripped, since it was not established that uv
  accepts hashes in a constraints file). What a user's install takes.
- **Why two:** a user's install should not carry pytest, mypy and black. **Why
  layered:** two independent resolves could pick different versions of a shared
  package, and then the runtime set would be one the suite never ran against. The
  dev resolve comes first because it is what the tests run in, and a constraint
  can only narrow. `lock-check` holds the runtime lock to the dev lock's versions
  and hashes, so the layering is checked, not assumed.

### 7.3 Platforms and Pythons

CPython 3.11 to 3.14 on Linux x86-64 (glibc 2.28 or later) and macOS 14 or later
on arm64: exactly where the probe's `install_outlook` says LIBRARY. All four
Pythons and not one, because the universal file costs nothing more per Python and
the unit has wheels for all four. Linux aarch64, where the floors install without
the library, is **not** covered: bootstrap with a lock refuses it in one sentence,
naming `--unlocked`. `tests/test_lock.py` holds pyproject's `environments`, the
probe's verdict and `lock.py`'s targets to each other over a grid.

### 7.4 What is built

- `make lock` (`scripts/lock.py lock`): refuses in one sentence without uv or
  without the index, then resolves the dev lock and the runtime lock within it,
  runs lock-check on the result, and replaces both files or neither. It keeps the
  versions the current lock holds unless `UPGRADE=1`. `UV=` names a uv not on PATH.
- `make lock-check` (`scripts/lock.py check`), offline, `packaging` only: every
  line one exact version with at least one sha256 hash and no pip options; every
  requirement of pyproject (runtime, `dev`, editable build) in the lock at a
  version its specifier allows, and with an entry on each target where the
  requirement's marker applies; imgui-bundle, imscribe and imtypeset at exactly
  the pin in both; pptmstr itself in neither; the runtime lock a subset of the dev
  lock's versions and hashes. `make check` runs it, and passes where no lock has
  been written yet. **It cannot check** that a transitive requirement is still
  what its parent asks for, or which wheel a hash belongs to: a requirements file
  names neither.
- `bootstrap.sh`: with `requirements-dev.lock`, stage 1 also asks `lock.py covers`,
  and stage 3 is `install --require-hashes --only-binary :all: -r
  requirements-dev.lock`, then `install --no-deps --no-build-isolation -e .`; the
  pip branch skips its upgrade of pip, the one unhashed fetch it would make. A
  kept `.venv`'s source-built imscribe or imtypeset is replaced from the lock's
  own lines (`lock.py subset`). Without a lock, or with `--unlocked` /
  `PPTMSTR_UNLOCKED=1`, the floors as before, with a line saying UNLOCKED and why.
  A kept `.venv` keeps packages the lock does not name; `rm -rf .venv` for exact.

### 7.5 The CI job (not built; to be boarded)

On Linux x86-64 in a glibc 2.28 image and on macOS 14 arm64, CPython 3.11 and
3.14, on every push and weekly: `./bootstrap.sh --no-window --unlocked && make
check` (the drift watch: what a fresh resolve gives today, which is how the mcp 2
crossing would have been seen the day it happened), and `./bootstrap.sh
--no-window && make check` (the lock installs and is green where it claims to).
A red unlocked run with a green locked one is the signal to `make lock
UPGRADE=1` deliberately.

### 7.6 Open

- **The lock is not written.** The user runs `make lock` on a machine with the
  network; a later task verifies and commits the two files. **2026-10-15:** not
  before the pins move to imscribe 0.2.0 and imtypeset 0.4.3 (§8.4).
- Whether `environments` accepts `python_version` beside the platform markers,
  and whether `required-environments` treats the `macosx_14_0_arm64` wheels as
  satisfying darwin arm64, were read and not run: the first `make lock` answers both.
- The 8 tests skipped in a fresh clone (§7, *Origin*) are the development
  override's: six `needs_a_built_pair` tests and one `.deps` pair test in
  tests/test_version.py, and tests/test_typeset_deps.py's hold of the installed
  imscribe to the commit `imscribe.ref` names. None is a test of the installed
  product, but the last is the only check that the published imscribe is that
  commit, and it never runs in an install from the index.

### 7.7 Sources (2026-10-14)

docs.astral.sh/uv: `concepts/resolution` (universal resolution, platform-specific
resolution, limited and required environments, minimum libc version, pre-releases);
`reference/settings` (`environments`, `required-environments`,
`minimum-libc-version`); `reference/cli` (`uv pip compile`: `--universal`,
`--generate-hashes`, `--python-platform` values and its macOS 13.0 default,
`--python-version`, `--only-binary`, `--constraints`, `--custom-compile-command`,
`--upgrade`, `--output-file`, pyproject.toml as a source; `uv pip install`:
`--require-hashes`, `--no-build-isolation`, `--reinstall-package`); `pip/compile`
("If a dependency is pinned it will not be upgraded on a subsequent compile run",
`--upgrade`, constraints files, pyproject.toml as input) and `pip/compatibility`
(no index URL written by default). Hatchling 1.32.4's
`build.py`, read from an installed copy.

## 8. The move to imscribe 0.2.0 on imtypeset 0.4.3 (2026-10-15)

**Origin:** the user, 2026-10-15: *"imtypeset 0.4.3 is published. Working on
publishing imscribe 0.2.0"* and *"prepare for imtypeset 0.4.3 with imscribe
0.2.0"*. **Status: trialled, not made.** The pins move when imscribe 0.2.0 is
on PyPI: pptmstr cannot require imtypeset 0.4.3 beside imscribe 0.2.0b1, whose
own requirement is `imtypeset==0.4.1.192900`. By pptmstr's builder (task
PREP-020). What is marked *run* was run by it, on Linux x86-64, CPython 3.11.2,
Xvfb and llvmpipe; the rest is from reading imscribe's and imtypeset's
repositories at the commits named, and says so.

### 8.1 The three pins and the marker

| | today | at the move |
|---|---|---|
| `imgui-bundle` | `==1.92.900` | unchanged |
| `imscribe` | `==0.2.0b1` | `==0.2.0` (no longer a beta; the "EXACT also because a beta" half of its comment in pyproject.toml goes) |
| `imtypeset` | `==0.4.1.192900; <marker>` | `==0.4.3.192900; <marker>`, the marker unchanged |

imscribe 0.2.0's requirements **as read from its pyproject.toml at `7b4700c`**,
the head of `experimental/imtypeset-adoption` and not yet the tagged commit
(its `ASSIGNMENTS.md` §0: the lead commits what is left, the owner tags that):
`imgui-bundle==1.92.900`, `imtypeset==0.4.3.192900; python_version >= '3.11'
and python_version < '3.15' and ((sys_platform == 'linux' and platform_machine
== 'x86_64') or (sys_platform == 'darwin' and platform_machine == 'arm64'))`,
`markdown-it-py>=3.0`, `fonttools>=4.50`. The marker is pptmstr's character
for character (compared). Its `imtypeset.ref` is `0bac066`, which is
imtypeset's `v0.4.3^{commit}` (run). The version string keeps its shape,
`0.4.3.192900`: the fourth number is the wheel's line, which
`tests/test_version.py` reads with `rsplit(".", 1)`, and the library reports
`0.4.3` (run, `pairing()`).

**Read the published file, not this table, at the move:** the Requires-Dist of
the wheel on PyPI is what `tests/test_version.py` holds pyproject.toml to.

### 8.2 The trial

A `git archive` of pptmstr `b97c2b7` with its two ref files pointed at
imscribe `7b4700c` and imtypeset `0bac066`; the library built by
`scripts/typeset_deps.py library` from local clones (no network) with
`IMTYPESET_BUNDLE_HEADERS=stand-in`, because the wheel's fork headers cannot be
fetched here. Its bytes are sha256 `fb0657ea…`, the hash imtypeset's own gate
recorded for a stand-in build of 0.4.3 (gate-043), so the build reproduced.
A `git archive` of imscribe at `7b4700c` first on `PYTHONPATH`,
`PPTMSTR_TYPESET_DEPS=1`, `IMSCRIBE_NO_TYPESET` unset. Every run's header
named imscribe 0.2.0 from the export and imtypeset 0.4.3 `0bac066` from the
pair, route `environment`, headers stand-in. Nothing in `pptmstr/` or
`scripts/` was changed for it. *Run.*

| run | passed | failed | skipped | xfailed |
|---|---|---|---|---|
| trial, with the library (`make check`: lint, mypy and lock-gate clean) | 11206 | 22 | 1 | 23 |
| trial, `IMSCRIBE_NO_TYPESET=1 make test` | 10485 | 20 | 744 | 3 |
| the lead's free data point: the normal `.venv` (imscribe 0.2.0b1 installed, the imtypeset 0.4.3 wheel installed beside it) | 11227 | 2 | 0 | 23 |
| the same, `IMSCRIBE_NO_TYPESET=1` | 10504 | 2 | 743 | 3 |
| the normal `.venv` again, by this builder, `make check` from an export of `b97c2b7` | 11226 | 2 (the lead's two) | 1 | 23 |
| the same, `IMSCRIBE_NO_TYPESET=1 make check` | 10503 | 2 (the same) | 744 | 3 |

Every row is the same 11,252 tests. The one skip more than the lead's in each of
the last two rows, and in the trial's, is the clone hold below: an export has
no `.deps/imscribe`.

The failures by cause:

- **The pins (the move's; 3 with the library, 3 without).**
  `test_env_floor.py::test_the_imscribe_that_is_imported_is_the_pinned_one`
  (0.2.0 against 0.2.0b1), and `test_version.py`'s
  `test_the_installed_imtypeset_is_the_pinned_one_and_requires_the_pinned_wheel`
  and `test_with_an_imtypeset_installed_the_report_names_it_its_route_and_its_headers`
  (the installed wheel is 0.4.3: "imtypeset 0.4.3.192900 is running and pptmstr
  pins 0.4.1.192900"). The last two are the lead's two.
- **Moved behaviour, which is A7 answered (the move's; 1).**
  `test_typeset_diffs.py::test_a_row_with_a_character_the_code_face_lacks_is_off_its_columns_where_they_hold`:
  `content_width` of the row `xπxЖx` is 136 = (7 + 10) × 8, where the test pins
  (5 + 10) × 8. The glyphs are where they were (0, 8, 24, 32, 48: two cells a
  borrowed letter, on the grid); the view now counts the cells it draws
  (imscribe R3-WIDE, `bd5aa71`).
- **The trial's own arrangement (18 with the library, 17 without), not the
  libraries.** 17 in `test_typeset_deps.py` hold what the Makefile exports to
  an environment with no `PYTHONPATH` of the caller's, and the trial gave one;
  `test_version.py::test_switched_off_the_two_variables_are_taken_out_and_said`
  reads `IMTYPESET_LIBRARY` from the test process itself, which the Makefile
  exported. All 18 pass in the same export with neither set (run: 77 passed,
  1 skipped). The skip is the hold of the installed imscribe to a clone in
  `.deps/imscribe`, which the trial has none of.
- **A regression of theirs: none found.**

**No strict xfail turned** (23 and 3, as before; no XPASS), and none was
expected to: P9 to P21 are not among 0.2.0's changes. **No warning turned red**
under `filterwarnings = ["error"]`: pptmstr passes no `cell=`.

The probes, each also run against the pair this checkout pins today (imscribe
0.2.0b1 installed, imtypeset 0.4.1 `e6160c0` built with stand-in headers), the
outputs compared line by line (*run*, 2026-10-15, load 2 to 31 from parallel
runs; nothing here is a time):

| probe | 0.2.0b1 on 0.4.1 | 0.2.0 on 0.4.3 |
|---|---|---|
| `measure_typeset_diff.py` (14 to 24 px) | — | identical apart from the pairing line: 0 glyphs off their column, 0 hit tests answering another character, the seam's cell 7.000015, 8.000009, 9.000003 at 14, 16, 18 px on both |
| `measure_typeset_advances.py --pptmstr` (56 pages) | a hit three quarters into a line's last character answers the next line's start: fence 6 of 180, host 7 of 188, on every page | **0 of 180 and 0 of 188 on every page**; every other line identical |
| `measure_heading_glyphs.py --paths stream,native --room forced --decoys 1` | 7 of 16 glyphs not their own size's bitmap, in one stream and one native scene | the same 7 of 16 (`4"Lufc"`) in the same two scenes |
| the same with `--keep` | 0 | 0 |
| the same with `--decoys 0` or `2` | 0 | 0 |
| `run_typeset_panes.py` (720 frames) | | identical counts: every pane typeset on 720 frames, `diff_adopted()` on all, the same seam calls and library draws |

So the second row is **P44 met** by imtypeset's H27, measured in pptmstr; the
third says the baked-size defect is Dear ImGui's and unchanged, and the guard
still answers it.

**How this differs from the lead's data point.** The normal `.venv` runs the
0.4.3 *wheel* (the fork's headers, attested by imtypeset's CI, found as a
distribution) under imscribe *0.2.0b1*, an environment no installer would
make, since 0.2.0b1 requires 0.4.1. It shows that the library alone moves
nothing but the two pin tests. The trial adds imscribe 0.2.0 and loses the
fork's headers; it shows that imscribe 0.2.0 adds exactly one red, A7's. Not
run by anyone: the pair of published wheels together, which is the move
itself.

### 8.3 What a host meets, and where pptmstr meets it

| change (whose) | where pptmstr meets it | at the move |
|---|---|---|
| diff view widths count drawn cells: a borrowed Greek or Cyrillic letter is two (imscribe R3-WIDE, `bd5aa71`; A7) | `diffs.unfit`, its refusal "the library's view would count one cell for a character it may set two wide"; tests above | **narrow it.** Drop the refusal for a character the code face lacks, and count such a character as two columns in the reach check, the rule `views.literal_unfit` already applies. Keep the lone-surrogate refusal (see P41 below). Rewrite `test_typeset_diffs.py`'s `..._is_off_its_columns_where_they_hold` to the cells the view now claims (136 at 16 px) and flip `..._a_diff_with_such_a_row_is_not_given_to_the_view_and_says_why` and `test_inbox_typeset.py::test_where_columns_hold_a_diff_with_a_character_the_code_face_lacks_is_the_rows_own` to a view; add a hit test a quarter and three quarters into π and Ж, which pptmstr has not measured (imscribe's windowed tests did) |
| `code_face_lacks(text)` is a supported name (imscribe P39) | `gate.code_lacks`, which asks the native module because the name was not promised | optional: call imscribe's; it raises `TypesetUnavailable` where the library cannot be asked, which the seam already handles |
| `reason()` names what is missing: `typesetter.ABSENT_REASON`, `SWITCHED_OFF_REASON` (imscribe P43) | `gate.not_installed` and its docstring ("its request P43 is open"); `tests/test_typeset_gate.py`'s `IMSCRIBES` literal, which the test monkeypatches in and so never meets the real sentence | replace `IMSCRIBES` with `typesetter.ABSENT_REASON`; keep pptmstr's own switched-off sentence, which names the value; correct the docstring |
| `replaced_characters` on every view (imscribe P41) | `diffs.unfit`'s lone-surrogate refusal, whose reason is that the library "would set it as U+FFFD and not name" it | nothing at the move: the store writes a surrogate out where session text enters, so a row holds none. Where one could arrive another way, the view can now be asked how many it set |
| a theme size that is not finite and positive raises `ValueError`; `TypesetView(src)` keeps `Theme.build()` at every scale (imscribe P47, `fc27d9d`) | `themes._build` (`Theme.at_pixel_height(roles.px(...))`), `gate.reader_hides` (`Theme.build()`) | nothing: `roles.px` is at least 1, `gate.drawn_size` answers `None` below a base of 1 and clamps at 512, and no theme is built before the first frame's base size (read; the trial built every theme with the library on) |
| the caret at a code line's end has its own position, and a drag ending past a line's end copies no trailing newline (imtypeset H27, D88) | `gate._row_columns` reads `caret_rect((0, len(row)))` of a row with no newline, which H27 does not change (the cell was 8.000009 at 16 px on both); a pane's mouse selection in a code block | nothing in code. **What the operator sees changes:** a press three quarters into a code line's last character stays on that line, and a drag to a line's end copies no line break. Nobody has looked |
| `device_glyph_advance` is the whole pixel (imtypeset H49) | `gate._row_columns` takes the farther of the view's cell and the laid row's | nothing: the seam's cell is the laid row's residue on both versions |
| a `TypeError`/`UnicodeEncodeError` from `from_blocks` names the block and field; **the class is unchanged** (imtypeset H36) | the seam treats any exception from a library call as the library stopping (`gate.into_library`); `gate._measured` hands `from_blocks` a fixed ASCII row | nothing. The premise that 0.4.3 raises `ValueError` there is refuted by imtypeset's own changelog and by imscribe's R3-043 measurement; imscribe replaces surrogates before they reach the library (P38, closed) |
| a fenced diff follows `set_document` and `set_theme`; a theme change drops its hand-set state (imscribe R3-HOSTS) | `gate.diff_fences`, the one hook held for the process | nothing in code; a fenced diff's expanded gaps and scroll go when the UI size changes. Nobody has looked |
| imtypeset installs on CPython 3.11 to 3.14 (H52, 0.4.2) | `scripts/probe.py`'s `install_outlook` says LIBRARY for 3.12 to 3.14 at the current pins, where `pip install` of imtypeset 0.4.1 answers "No matching distribution" (read; H52) | the move makes the probe true. Its sentences name `0.4.1.192900` in eight places: move them with the pin, or read the pin from `pairing.IMTYPESET_PIN` |
| H25, H28, H37, H38, H39, H41, H42, H45, H46 (imtypeset, header sentences and defects pptmstr does not reach) | none found by reading | nothing |

### 8.4 The move, as a checklist

When `pip index versions imscribe` (or PyPI's JSON) shows 0.2.0:

- [ ] Read 0.2.0's Requires-Dist from the published wheel; it should be §8.1's.
- [ ] `pyproject.toml`: `imscribe==0.2.0`, `imtypeset==0.4.3.192900` under the
      same marker; the comments that say 0.2.0b1 is a beta.
- [ ] `pptmstr/pairing.py`: `IMSCRIBE_PIN = "0.2.0"`, `IMTYPESET_PIN =
      "0.4.3.192900"`, and the docstring naming `imscribe==0.2.0b1`.
- [ ] `imscribe.ref`: `git rev-parse "v0.2.0^{commit}"` of imscribe, and its
      comment (tag v0.2.0). `imtypeset.ref`: `0bac06608be358aec7cf9a3ee72c6abc623146e3`
      (v0.4.3), and its comment.
- [ ] The tests that name the old versions as data: `tests/test_version.py`,
      `test_typeset_deps.py`, `test_typeset_gate.py`, `test_lock.py`,
      `test_probe.py` (each a string `imtypeset 0.4.1.192900`, `0.4.1` or
      `imscribe==0.2.0b1`). Most are labels a test hands in and would pass
      unchanged; move them so the tree says one version.
- [ ] `scripts/probe.py`: the eight sentences naming `0.4.1.192900`, and its
      "read 2026-10-13" line, after rereading PyPI's file lists for the two
      new versions.
- [ ] `diffs.unfit` narrowed and its three tests moved (§8.3, first row).
- [ ] `tests/test_typeset_gate.py`: `IMSCRIBES` to `typesetter.ABSENT_REASON`;
      `gate.not_installed`'s docstring.
- [ ] The docstrings that quote a measurement "on imscribe 0.2.0b1 and the
      0.4.1.192900 wheel" (`diffs.py`, `gate.py`, `themes.py`,
      `test_baked_sizes.py`) stay as the record of when they were measured,
      or are re-measured; not rewritten to a version they were not taken on.
- [ ] `README.md`'s install paragraph.
- [ ] `bootstrap.sh` into a fresh `.venv` on 3.11 and on a later CPython
      (the move is what makes 3.12 to 3.14 installable), then `make check`
      both ways: expected green, with the strict xfails as they are.
- [ ] `HANDOFF-advtext.md`: close P44 (§8.2's measurement), and A7 as
      answered by R3-WIDE once `diffs.unfit` is narrowed; P39, P41, P43 and
      P47 as built against.
- [ ] Then the lock, below.

**The lock is resolved after the move, and not before it.** Today's pins
include imtypeset 0.4.1, whose files on PyPI all carry `Requires-Python
==3.11.*` (H52). `make lock` resolves for CPython 3.11 to 3.14 and requires an
entry on every target the marker covers (`[tool.uv]`'s `environments` and
`required-environments`, §7.1), so a resolve today would either fail for 3.12
to 3.14 or, with the environments narrowed to 3.11, write a lock for one
Python that `tests/test_lock.py`'s grid would refuse. Either way it is replaced
within days by the lock at the new pins. *Inference from reading uv's
documentation and H52; no uv was run, and nothing was resolved.* So LOCK-B
waits for the move, and the user's `make lock` is asked for once, after it.

### 8.5 Not run

The published imscribe 0.2.0 (it does not exist yet); the fork's headers
(not fetchable here); CPython 3.12 to 3.14; macOS; a GPU; a display whose
density is not 1; anyone looking at the pictures `run_typeset_panes.py`
wrote. imscribe's `7b4700c` is not necessarily the commit that will be tagged.
