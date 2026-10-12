# A Mac install is a lock before it is a bundle

**Dated:** 2026-10-11 ·
**Status:** a recommendation; nothing built. **From reading, by pptmstr's
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
- Its cost is real and is the user's to weigh: it is a terminal install. If
  "a Mac supported install" means a double-clickable application, (b) is the
  step before it and not the destination. **Not asked of the user; recorded
  here as open.**

## 4. What pptmstr must do whatever the shape

| | today (read) | needed |
|---|---|---|
| pins | `imgui-bundle>=1.92.900`, a bare `advtext` | exact, in one tracked manifest of the four-part unit (the `DEPS` task starts it) |
| version | `__version__` in `pptmstr/__init__.py` and `version` in `pyproject.toml`, two literals nothing ties | one source; `pptmstr --version` prints it with the library pairing and the reason when not drawing, without a window |
| a refused sidecar | `theme._load_typeset_faces` swallows every exception; "not installed" and "refused" are the same non-empty string | the reason shown once, in one persistent place; never a traceback (P1) |
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

Nothing after that on the macOS side can be verified from this machine.

## 6. Sources

PyPI JSON for `imgui-bundle` 1.92.900 and `claude-agent-sdk` 0.2.165;
pyinstaller.org (run-time information, feature notes, usage);
docs.astral.sh/uv (tools); docs.brew.sh (language-specific formulae);
sparkle-project.org (documentation, programmatic setup, sparkle-cli);
briefcase.beeware.org (macOS); code.claude.com/docs (authentication,
sandboxing). py2app and Nuitka were not read.
