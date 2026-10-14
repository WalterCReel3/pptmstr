# A Mac install is a lock before it is a bundle

**Dated:** 2026-10-11 ·
**Status:** a recommendation; §5's first step is built (2026-10-12), and the machinery for its lock (2026-10-14, §7) with no lock yet written. **From reading, by pptmstr's
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
  network; a later task verifies and commits the two files.
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
