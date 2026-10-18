# An installer carries a built environment, and one manifest says when to move

**Dated:** 2026-10-17 ·
**Status:** a plan; nothing in it is built. The upgrade seam (§3) is designed here and
deliberately not coded: the lead's decision, recorded in §3.7, is that a stub with no
consumer would precede the vehicle that calls it, so this record ends with the first
buildable step that adds it (§7) ·
**Written by:** pptmstr's builder (task PLAN), from reading the five research results
on the board (R-PY, R-WIN, R-FLATPAK, R-DEB, R-PRIOR), their notes, the concerns
relayed by the lead, and the tree. **The writer ran nothing**; §3.5's symlink
measurement was folded in afterwards (task PLAN-FOLD) from PROBE-PREFIX's run, and
REVIEW's findings (reviewer-3, 2026-10-17) were folded in by task PLAN-FIX, each marked
*REVIEW* with the basis the reviewer gave it: *web* (read that day through a
summarising fetch), *read* (the tree or board), *recollection* or *inference*. Every factual claim
carries the basis its researcher gave it: *run* (executed, and by whom), *read* (from
a named page or file; web pages were read through a summarising fetch), *search*
(a search-result summary, weaker than a page read), *recollection*, or *inference*.
Where this record reasons past the evidence it says *inference (PLAN)* ·
**Origin:** the user, 2026-10-17 — *"I would like to start devising ways to build
pptmstr for MacOS, Linux and Windows. Mac and Windows would be standard application
install processes for desktop software. Linux would be a published flatpak. There
_must_ be posturing or stubbed hooks for easy upgrades."* Then, during the round:
*"If windows is a stretch then we punt and go with Mac/Linux for this beta phase."*,
*"Also we publish a simple PyPI for the true Python userbase."*, after R-FLATPAK's
findings *"Let's not force flatpak. A custom `.deb` as a full fallback and there's
always PyPI as the alternate."*, and *"This might be one to research other python
application that have traditional application installs and see what the conventions
are."* Three questions came with the first message; they are answered in §5 ·
**Extends:** [`2026-10-11-a-mac-install-is-a-lock-before-it-is-a-bundle.md`](2026-10-11-a-mac-install-is-a-lock-before-it-is-a-bundle.md)
(the "mac doc"). §2 below engages its §2–§3 recommendation directly; nothing here
re-decides its §7 (the release lock) or §8 (the pins) ·
**Read with:** [`2026-10-08-settings-have-one-place.md`](2026-10-08-settings-have-one-place.md),
for where the seam's one setting goes.

---

## 1. The channels

| channel | vehicle | blocked today by | what it would take |
|---|---|---|---|
| **macOS** | a `.dmg` holding a `.app` whose payload is a real CPython (python-build-standalone) with site-packages installed into it from `requirements.lock`, built and signed in CI (§2) | `requirements.lock` not written (mac doc §7.6); no Apple Developer identity; H1 (whether the typeset sidecar works on macOS) open, which degrades and does not block | the lock; an Apple account (§6); the CI job (§4); a person at a Mac |
| **Linux** | a custom `.deb`: the same interpreter-plus-site-packages tree under `/opt/pptmstr/<version>/`, built with `dpkg-deb` (R-DEB) | `requirements.lock` not written | the lock; the CI job; a decision on the claude binary's redistribution (§6) |
| **PyPI** (both) | `uv tool install pptmstr` or `pipx install pptmstr` | the name is unregistered and nothing has been published | trusted publishing (§4); the `requires-python` cap (§1.3) |
| **Windows** | deferred out of the beta | R-WIN's three blockers (§1.4) | a port of the command runner and a containment story; not packaging work |
| **Flatpak** | dropped | measured: no user namespaces inside a Flatpak (§1.5) | §1.5's list |

R-PRIOR's survey grounds the shape of each. Where pptmstr departs from what the
surveyed apps do, the row in §1.6 says so and why.

### 1.1 macOS

The vehicle and why are §2. What this channel carries from the research:

- **Floor: Apple Silicon, macOS 14.** Decided by imgui-bundle 1.92.900's wheels, all
  `macosx_14_0_arm64` (mac doc §1, *read*).
- **The command runner is weaker on macOS than on Linux.** Its identification of a
  command's processes reads `/proc/<pid>/environ`, and on any machine but Linux "no
  look is made" (`commandrun.py` module docstring, "Any machine but Linux", near l.182;
  *read* by R-WIN's builder and by this writer). A break-glass command's survivors are
  therefore not identified on a Mac. That is a product property of the Mac build, not
  a packaging defect, and it should be in the Mac release notes.
- **Credentials:** Claude Code keeps its login in the Keychain on macOS, so the
  launcher's model list falls back (mac doc §4, *inference* there). Unchanged.
- **The sandbox wording** names bubblewrap and socat; macOS uses Seatbelt (mac doc §4).
  Unchanged.
- **The `macos-14` hosted runner is deprecated** while macOS 14 is the floor (R-PY H4,
  *read* github.com/actions/runner-images): deprecated 2026-07-06, **retired
  2026-11-02, with brownouts during October 2026** (*web*, REVIEW,
  actions/runner-images#13518). docs.github.com still lists the label with no notice.
  §4 says what tests the floor after it goes.

### 1.2 Linux: the `.deb`

R-DEB's verdict, adopted:

- **Shape:** python-build-standalone CPython at `/opt/pptmstr/<version>/python/`, with
  `pip install --require-hashes --only-binary :all: --no-deps -r requirements.lock` and
  then pptmstr's wheel `--no-deps`. **No venv at all**: the interpreter is private, so
  the relocation question (R-PY H1) does not arise. `/usr/bin/pptmstr` is a short `sh`
  wrapper running `python3 -I -m pptmstr`, not a pip console script, whose shebang
  would be absolute (*recollection*, R-DEB).
- **Why this shape is safe for the native pair** (*run*, R-DEB, Debian 12):
  `libimtypeset_bundle.so` has RUNPATH `$ORIGIN/../imgui_bundle` and imgui-bundle's
  module `$ORIGIN/./:$ORIGIN/../imgui_bundle.libs`, so the pair is relocatable as long
  as both sit in one site-packages. No patchelf.
- **Nothing in the build may strip.** *Run* by R-DEB and reproduced by PROBE-CLI:
  `strip --strip-unneeded` on `claude_agent_sdk/_bundled/claude` leaves a binary whose
  `--version` prints `1.4.3` and whose `--help` is Bun's. The embedded Claude Code is
  gone. So: `dpkg-deb` directly, not debhelper (whose `dh_strip` strips every ELF —
  *recollection*, R-DEB). This applies equally to the macOS build and to any freezer.
- **Build:** `dpkg-deb --root-owner-group -Zxz` as an ordinary user gives root-owned
  members with no fakeroot, and two builds under one `SOURCE_DATE_EPOCH` were
  byte-identical (*run*, R-DEB). xz, not zstd, for bullseye's dpkg (*recollection*).
  Estimated size 120–130 MB (*run* for site-packages at 114.9 MB compressed;
  interpreter size *recollection*).
- **Byte-compile at build time** with `SOURCE_DATE_EPOCH` set (CHECKED_HASH,
  reproducible; *run*, R-DEB reading `py_compile` on 3.11.2), since users cannot write
  `.pyc` into `/opt`.
- **Depends:** `libc6 (>= 2.28), libstdc++6, libgcc-s1, zlib1g, libx11-6, libxext6,
  libxcursor1, libxi6, libxinerama1, libxrandr2, libgl1, bubblewrap, socat, git`;
  Recommends `libxcb1, libxrender1, libxxf86vm1` (*run*: sonames mapped with `dpkg -S`
  on Debian 12). glibc 2.28 is imgui-bundle's floor, not imtypeset's (*run*, readelf).
  So Debian 11+ and Ubuntu 20.04+ (*read*, packages.debian.org, launchpad). x86-64
  only: no imtypeset wheel for arm64.
- **Ubuntu 24.04 and later block bwrap's user namespaces by default** (*read*,
  code.claude.com/docs/en/sandboxing and discourse.ubuntu.com). `Depends: bubblewrap`
  is therefore not enough there for the contained rungs: this is R-FLATPAK's failure
  mode on the `.deb`'s main target. Adopted for the beta: R-DEB's option (ii), a check
  that reads `kernel.apparmor_restrict_unprivileged_userns` and says the sentence and
  the documented remedy; shipping an AppArmor profile is a system-wide grant and stays
  an opt-in for later (§6). *Not tested on an Ubuntu machine.* **That check lives in
  the app, not the probe** (REVIEW M4, *read*): `scripts/probe.py` has no bwrap,
  userns or AppArmor check, and the wheel ships only `packages = ["pptmstr"]`, so the
  probe is not in the `.deb` (or in a PyPI install) to run on the user's machine.
  Nothing in `pptmstr/` reads the sysctl today either (*read*, PLAN-FIX, by grep:
  `sandbox.py` relies on `failIfUnavailable`). It is a step of its own in §7, and it serves the PyPI
  channel on Ubuntu 24.04 as much as the `.deb`.
- **Desktop entry:** `/usr/share/applications/dev.holond.pptmstr.desktop` and hicolor
  icons. A sample passed `desktop-file-validate` (*run*, R-DEB). `StartupWMClass` must
  equal GLFW's WM_CLASS for pptmstr's window, which was **not measured**.
- **Settings stay** at `$XDG_CONFIG_HOME/pptmstr` or `~/.config/pptmstr`; the package
  writes nothing under `$HOME`, and purge leaves them.
- **The unit all-or-nothing inside a running process** — §3.5, because it is the
  applier's problem as much as the package's.

### 1.3 PyPI

- **Advertised routes:** `uv tool install pptmstr` and `pipx install pptmstr`, each an
  isolated environment (*read*, docs.astral.sh/uv/concepts/tools, pipx.pypa.io).
  Plain `pip install` into a shared environment is not advertised: the three exact pins
  collide with anything else wanting imgui-bundle (*read*, pip's resolution docs, and
  *inference*).
- **This channel is the mac doc's shape (b) as it was written** — a terminal install
  of a venv made by a resolver on the user's machine. It is the right shape for "the
  true Python userbase", who expect exactly that, and the wrong one for a desktop
  install (§2).
- **It does not redistribute the claude binary.** The user's resolver fetches
  `claude-agent-sdk`'s wheel from PyPI, which Anthropic publishes.
- **It cannot carry the hashed lock.** `uv tool upgrade` keeps the exact pins, so the
  three-part unit plus pptmstr moves together (*inference*, R-PY), but it re-resolves
  the loose dependencies (`anthropic`, `mcp`, and `claude-agent-sdk>=0.2.158`, which
  is a floor in pyproject.toml, so the bundled CLI version is not pinned on this
  channel — *read*, pyproject.toml). **2026-10-17, later:** `anthropic` is no longer one
  of them. It was removed from pyproject.toml together with the model probe, its only
  importer (task MODEL-STATIC; see the addendum to
  [`2026-09-25-the-model-list-outlives-the-build-that-verified-it.md`](2026-09-25-the-model-list-outlives-the-build-that-verified-it.md)).
  That is the mac doc §7's drift, accepted for this
  channel. `--with-requirements` as a way to apply the lock was **not read** (R-PY).
- **Recommendation, not done: cap the published `requires-python` below 3.15.** Today
  it is `>=3.11`, open-ended. `uv tool install` takes "the first [Python] it finds"
  (*read*, uv tools guide); on CPython 3.15 the imtypeset marker is false and
  imgui-bundle 1.92.900 has no cp315 wheel, only a 48.9 MB sdist (*read*, PyPI JSON),
  so such an install compiles imgui-bundle or fails, instead of refusing with a reason
  (*inference*, R-PY; not run). A cap fails at resolve time and names the reason; it
  is this channel's equivalent of the lock's `environments`. It belongs in the PyPI
  step of §7, and it moves with `IMTYPESET_PYTHONS` in `pairing.py`, so a test should
  hold the two together. **Every published version must carry the cap, including the
  first one that only holds the name** (§6.4): uv's resolution docs say that in
  universal resolution it "ignores upper bounds entirely", because resolvers
  "backtrack to the first published version that omits the upper bound" (*web*,
  REVIEW, docs.astral.sh/uv/concepts/resolution). One uncapped version on PyPI is
  the version a 3.15 resolver falls back to, and the cap on every later one then
  protects nothing. **Open:** whether `uv tool install` on an exact 3.15 interpreter
  refuses a capped release or backtracks the same way was not established (REVIEW).
- **Size:** pptmstr's own wheel is far below PyPI's 100 MB default (*inference* from
  a file listing; no wheel was built). The SDK's 104–117 MB wheels are Anthropic's.
- **The name is unregistered** (*read*, `https://pypi.org/pypi/pptmstr/json` gave 404
  on 2026-10-17), and a pending trusted publisher does not reserve it (*read*,
  docs.pypi.org). §6.

### 1.4 Windows: deferred

R-WIN's verdict is **STRETCH**, so by the user's rule Windows is out of the beta. The
blockers, as R-WIN gave them (*read*, code; nothing was run on Windows):

1. **pptmstr cannot import on Windows.** `commandrun.py` has `import pwd` at module
   level, reached from `app.py` through `ui/inbox` and `ui/command_view`; `app.py`'s
   module-level `EXIT_SIGNALS` names `signal.SIGHUP`.
2. **Containment does not exist on native Windows.** "On native Windows, Claude Code
   runs commands unsandboxed", and with `failIfUnavailable` it exits at startup
   (*read*, code.claude.com/docs/en/sandboxing; confirmed *web*, REVIEW). Only the
   attended policies could run; the autonomous rung needs WSL2, where pptmstr is the
   Linux build under WSLg. **The Ubuntu 24.04+ AppArmor restriction (§1.2) applies
   there too**: the sandboxing page says so, "including inside WSL2" (*web*, REVIEW),
   so a WSL2 route on an Ubuntu 24.04 distribution needs the same check and remedy.
3. **The lock and the typeset library exclude Windows by construction**: `[tool.uv]`
   names only linux x86_64 and darwin arm64, and imtypeset 0.4.3.192900 has no Windows
   wheel and no sdist (*read*, PyPI JSON).

Beyond those, R-WIN lists POSIX-only paths (`os.O_NOFOLLOW`/`O_DIRECTORY`/`O_CLOEXEC`,
`select.poll`, `os.killpg`, `/proc`, POSIX-shell parsing in the approval gate, which
does not model the CLI's PowerShell tool). **A Windows port is a port plus a
containment decision, not an installer choice.** The installer answer for when it
comes back is in §5.1. Note also that `uv tool install pptmstr` on Windows would
*resolve* (imgui-bundle has win_amd64 and win_arm64 wheels; the marker drops
imtypeset) and then fail at import (*inference*, R-PY): a PyPI page should say
"macOS and Linux" plainly, and the `Operating System` classifiers should not claim
Windows.

### 1.5 Flatpak: dropped, and what would bring it back

Recorded from R-FLATPAK:

- **Measured** (the operator ran it, cmd-20261017T184110Z-cbbbcf49b010, freedesktop
  26.08 runtime on this machine): inside a Flatpak, `unshare(CLONE_NEWUSER)` gives
  `[Errno 1] Operation not permitted`, and the runtime has no bwrap, socat or git.
- **Read** (flatpak's `flatpak-run.c`, `setup_seccomp`): the block is unconditional
  ("Don't allow subnamespace setups"); no permission re-enables it. Bundling bwrap
  does not help. Claude Code's Linux sandbox is bubblewrap, and pptmstr sets
  `failIfUnavailable`, so the AUTONOMOUS rung cannot run (which way it fails — exit at
  start or each Bash call refused — was **not run**).
- **Host toolchains:** without `flatpak-spawn --host` (which needs
  `--talk-name=org.freedesktop.Flatpak`, a declared sandbox escape) the agent's Bash
  sees the runtime's tools, not the user's. VS Code's Flathub manifest does exactly
  that (*read*), and its users' complaints are about host SDKs (*search*).
- **Flathub policy** (*read* by R-FLATPAK's builder on docs.flathub.org; wording
  confirmed *web* by REVIEW, under "Generative AI policy"): IDE-like and broad-scope
  development tools "are generally not well-suited", apps relying on host components
  for core functionality "will not be accepted", and "Flathub manifests must not
  contain AI-generated or AI-assisted content", nor may AI tools "open or automate
  Flathub submission pull requests". Two further rules (*web*, REVIEW): "Submitters
  must disclose any AI-generated code, documentation, packaging", and submitters
  "must not request AI-agent reviews".
- **What would have to change for it to come back** (*inference (PLAN)*): one of
  (a) Flatpak permits nested user namespaces, or Claude Code gains a sandbox backend
  that works inside one (the portal's sub-sandbox has no domain allowlist or proxy,
  *read*, so it is not that today); or (b) pptmstr accepts running the CLI on the host
  through `flatpak-spawn --host`, in which case Flatpak contributes only a GUI bundle;
  and, for Flathub specifically, (c) a manifest and PR written by hand by the
  operator, or a self-hosted repository (`.flatpakref`), which escapes Flathub's rules
  but not (a) or (b). A hand-written manifest does not end Flathub's AI rules: the
  disclosure rule covers AI-generated *code*, and code written with agents, as this
  round's is, would have to be disclosed in a submission (*inference (PLAN-FIX)* from the
  rule's wording as REVIEW read it).

AppImage was named by R-FLATPAK as an alternative and **was not researched**.

### 1.6 Where pptmstr departs from convention, and why

R-PRIOR surveyed eleven shipping Python desktop apps (*read*, per-app URLs in its
result). Its conventions, counted only where several apps share them, and pptmstr
against each:

| convention (R-PRIOR) | pptmstr | why |
|---|---|---|
| C1. macOS: a `.dmg` holding a **frozen** `.app` (7 of 10) | a `.dmg` holding a `.app` with a **real interpreter** — the minority shape (Thonny's `.pkg`; Spyder's constructor) | every freezer makes `sys.executable` something other than a Python (R-PY H2), and the import gate (mac doc H15) needs a real one. Thonny is the precedent: a populated, relocatable Python.framework shipped in the package (*read*, thonny/packaging/mac) |
| C2. signing and notarisation in CI | the same | — |
| C3. updates are notify-and-download (calibre, kitty, Electrum, Cura, Picard; Anki in part); no surveyed app uses Sparkle | the same for the beta, on every channel (§3) | conforms. Electrum's signed version feed is the precedent for authenticating it |
| C4. Linux: upstream tarball or AppImage plus an installer script; `.deb` is rare (Tribler; Kolibri's apt repo) | a `.deb` | the user's decision, and justified on the evidence: pptmstr needs host bwrap, socat and git, which a `.deb` can declare and a tarball cannot (R-PRIOR P5, *inference*) |
| C5. a PyPI install alongside (Anki, Picard, Thonny, Electrum, Orange, Spyder) | the same | conforms; Picard publishes with `pypa/gh-action-pypi-publish` on tags (*read*) |

## 2. macOS: the mac doc's (b), built in CI, in (a)'s bundle

The mac doc §3 recommends **(b), an installed interpreter from a lock, first**, and
**(a), a frozen `.app` + Sparkle, later as a wrapper round the same lock**. It records
as open whether "a Mac supported install" means a double-clickable application, and
says that if it does, (b) "is the step before it and not the destination." Three
things since then bear on that, and this section engages each.

**2.1 The open question is answered.** The user, 2026-10-17: *"Mac and Windows would
be standard application install processes for desktop software."* The destination is
a double-clickable application. The mac doc's (b) as written — `uv tool install` or
pptmstr's bootstrap, a terminal install — survives, as the PyPI channel (§1.3).

**2.2 What (b) meant by "the updater builds the next venv beside the current one" is
what Anki's users rejected.** From 25.07 to 26.05 Anki shipped almost exactly that: a
Rust launcher drove a bundled `uv` to build a venv in a per-user folder on first
launch (`uv.exe sync --upgrade --managed-python --python 3.13.5` in its error logs).
26.05 replaced it with Briefcase, and the maintainers gave **no reason** (*read*,
forums.ankiweb.net/t/new-online-installer-launcher/62745 and /t/anki-26-05-beta-1/69707,
via R-PRIOR). The users' recorded complaints: a terminal window opens to launch a GUI
app; installs ran 10–20+ minutes; first launch needs the network and fails in some
regions; a requires-python bump stranded launcher users so they could not update
in-app (*read*, forums.ankiweb.net/t/…/66080). R-PRIOR's inference, which this record
adopts: **what was rejected is building the environment on the user's machine, not an
interpreter plus a lock.** Thonny ships the interpreter already populated, and Spyder
applies lock files to a real interpreter's tree (*read*). The mac doc's (b) table cell
names where the venv is built: "`uv tool install` or pptmstr's own bootstrap", both
of which build on the user's machine (REVIEW M2, *read*, mac doc §2). **This record
reverses that for the desktop channel: the environment is built in CI, never on the
user's machine.** It is a change to (b), not a gap in it being filled, and the reason
is Anki's: the shape the mac doc chose is the one Anki's users rejected.

**2.3 But a venv cannot be built in CI and shipped.** Venvs are "not considered as
movable or copyable"; script shebangs are absolute (*read*, docs.python.org venv);
`uv venv --relocatable` guarantees only console and GUI scripts and leaves `home`
naming a base interpreter that must exist at the target (*read* and *inference*,
R-PY H1). So the thing built in CI is **not a venv**: it is a base interpreter
(python-build-standalone, "Portable, can be installed into any path", *read*,
github.com/astral-sh/python-build-standalone) with site-packages installed directly
into it from `requirements.lock` — the same tree the `.deb` ships (§1.2), and the
shape Thonny ships with a python.org framework instead (R-PRIOR P2).

**So the macOS vehicle is (b′):** (b)'s real interpreter and hashed lock, in (a)'s
bundle shape.

**This undoes the mac doc's reason for building (b) first, for the desktop channel.**
The mac doc §3 ordered (b) before (a) because "it is the only shape that does not wait
on H15", and its (b) column needed no signing. (b′) takes on (a)'s signing (below) and
the half of H15 where the stamp is written inside a signed bundle (below, open). So
for the `.dmg` the ordering argument no longer holds, and (b′) is not "(b) first" in
the mac doc's sense: it is a different trade, accepted because the user's answer
(§2.1) makes the double-clickable application the destination (REVIEW M2,
*inference*). The argument still holds for the PyPI channel, which is (b) as written
and waits on neither (§1.3, §7).

- `pptmstr.app/Contents/Resources/<tree>`: python-build-standalone aarch64 CPython
  (3.11–3.14; one minor, chosen per release) with site-packages from
  `requirements.lock` and pptmstr's wheel, byte-compiled at build time.
- `Contents/MacOS/pptmstr`: a thin launcher that execs the tree's `python3 -I -B -m
  pptmstr`. Whether a shell script is acceptable as `CFBundleExecutable` for signing
  and notarisation, or a small compiled launcher is needed, was **not researched**.
- **Nothing may write into the signed bundle** (REVIEW M3, *inference*). A `.app`
  dragged into place is owned by the user, so a module whose `.pyc` is missing or
  stale would get `__pycache__` written inside the bundle after signing. So every
  `.pyc` is compiled in CI before signing (as above), and the launcher passes `-B`
  so that anything the build missed is compiled in memory and not written. Nothing
  in `pptmstr/` sets `-B` or `PYTHONDONTWRITEBYTECODE` today (*read*, REVIEW). The
  mac CI job should assert that launching the signed `.app` leaves no new file in it.
- `sys.executable` is a real interpreter, so **the import gate's `sys.executable`
  concern (mac doc H15, §2 row "the import gate") does not apply** (*inference*,
  R-PY H2, consistent with the mac doc's own (b) column). **What this record does not
  settle:** the mac doc's (a) column names a second gate problem — "the stamp is
  written inside a signed bundle". (b′) puts the tree inside a signed bundle too.
  Where the gate writes its stamp, and whether that location is inside `.app`, is a
  question for imscribe/imtypeset (H15), and is **open** here.
- **Signing is now a cost (b) did not have.** The mac doc's (b) column says "none to
  exist". (b′) needs a Developer ID, hardened runtime and notarisation of every Mach-O
  in the tree, including Anthropic's claude binary and the imtypeset library, as (a)
  did (mac doc §2, *inference* there). Whether re-signing the claude binary preserves
  its embedded payload on macOS — the Linux `strip` result (§1.2) is the reason to ask
  — was **not tested**. The CI job verifies it (§4). **Open** (REVIEW L4,
  *recollection, unverified*): if Anthropic's macOS claude binary is already signed
  with a Developer ID, it may not need re-signing, and the RECORD-hash check (§4)
  would then work on macOS too. `codesign -dv` on the wheel's binary settles it.
- **The sidecar (H1)** is unchanged: open; the Mac ships with pptmstr's own renderers
  whatever the answer.
- **Settings location**: the mac doc §4 says moving to `~/Library/Application
  Support` "needs a migration, so decide before the first Mac user". That is now
  before the first `.dmg` (§6).

**2.4 Briefcase is not reopened here, and one fact could reopen it.** R-PY recalls
that Briefcase runs the app through its own stub, so `sys.executable` is the stub and
the gate applies (*recollection; unconfirmed*). R-PRIOR could not establish whether
Anki 26.05's Briefcase build keeps `sys.executable` a real interpreter (*search only*).
**Open, not settled:** if Briefcase does keep a real interpreter, it is a maintained
packager of exactly (b′)'s shape, and it should be weighed against assembling (b′) by
hand. A read of Briefcase's macOS stub source settles it.

**2.5 What the mac doc's §5 already lists is unchanged** — exact pins, one release
manifest, a version line that reports the pairing, a refusal as a sentence, a staged
switch with rollback. §3 is that manifest and that switch.

## 3. The upgrade seam

The part the user said must exist. Design only (§3.7). Small on purpose: **a check
that tells the user, on every channel, and an applier that is the channel's own
updater**. No channel self-installs in the beta; that is the convention (R-PRIOR C3)
and it keeps pptmstr out of the business of replacing its own files while running.

### 3.1 What the app knows: which channel installed it

A **channel marker** written by each packaging step into the installed package
directory, `pptmstr/_channel.json`:

```json
{"channel": "deb", "feed": "", "built_from": "<commit>", "built": "2026-11-01"}
```

- `channel` is one of `mac`, `deb`, `pypi`. **Absent for a checkout**, and absence
  means "no check": a developer's tree never phones anywhere.
- `feed` is the manifest URL this build checks; **empty in the stub**, which is what
  makes the check a no-op until a channel URL is configured.
- The file is gitignored and generated: the `.deb` and `.app` builds write it into the
  installed tree after `pip install`; the PyPI build writes it before `uv build`.
  Whether hatchling includes a gitignored file through its `artifacts` option is
  *recollection* and must be checked at that step.
- **Why a file and not a heuristic on `sys.prefix`:** R-PY notes that telling `uv
  tool` from `pipx` from a checkout would need prefix heuristics whose directories
  were not read. A marker the packager writes is a fact the packager knows; a guess
  from paths is a default where the honest answer is a refusal (STYLE §3). For PyPI
  the marker says `pypi`, and the sentence names both `uv tool upgrade pptmstr` and
  `pipx upgrade pptmstr` rather than guessing which one.

### 3.2 One manifest format for every channel

One JSON document per release, published with it (`manifest.json`), plus a detached
signature (`manifest.json.sig`):

```json
{
  "format": 1,
  "version": "0.2.0",
  "released": "2026-11-01",
  "notes": "https://…/releases/tag/v0.2.0",
  "minimum_from": "0.1.0",
  "unit": {"imgui-bundle": "1.92.900", "imscribe": "0.2.0", "imtypeset": "0.4.3.192900"},
  "requires": {"python": ["3.11", "3.14"], "macos": "14.0", "glibc": "2.28"},
  "channels": {
    "mac":  {"url": "https://…/pptmstr-0.2.0-arm64.dmg", "sha256": "…"},
    "deb":  {"url": "https://…/pptmstr_0.2.0_amd64.deb", "sha256": "…",
             "also": [{"url": "https://…/pptmstr-0.2.0_0.2.0_amd64.deb", "sha256": "…"}]},
    "pypi": {"commands": ["uv tool upgrade pptmstr", "pipx upgrade pptmstr"]}
  }
}
```

- **`version` is `N.N.N` only.** Prereleases and yanked releases never enter the feed,
  so comparison is a tuple of ints and needs no `packaging` at runtime (`packaging` is
  not among pyproject's runtime dependencies, *read*). For PyPI, the feed is this same
  manifest, not PyPI's JSON: R-PY found the JSON API does not say whether
  `info.version` excludes prereleases or yanked releases (*read*, docs.pypi.org/api/json),
  and one feed for three channels is one parser.
- **`minimum_from`** is the oldest version that can move straight to this one. Below
  it the notice says to install fresh, not to upgrade. This is where a settings or
  board-log format change that cannot be read across a skipped release is declared.
- **`requires`** answers Anki's stranding (R-PRIOR P3): the check compares the
  running machine against the next release's floors (Python minor for PyPI, macOS
  version, glibc) before it says "upgrade", and says why it cannot when it cannot.
  For PyPI this is the `requires-python` cap (§1.3) seen from the other side.
- **`unit`** names the three pins from `pairing.py`, so the notice can say when the
  typeset library moves. **The unit is never upgraded in parts by this seam**: every
  applier replaces an artifact that carries all four parts (the `.dmg`, the `.deb`
  pair, or a pptmstr whose exact pins bring the other three). The seam has no path
  that installs one package.
- **`sha256`** lets a later applier that downloads verify what it downloaded. The
  beta appliers do not download (§3.4); the user's browser, Gatekeeper and dpkg do.
- **No link the notice shows comes from the manifest** (decided by PLAN-FIX on REVIEW
  M1). The manifest is unauthenticated in the beta, and the `.deb` notice tells the
  user to `sudo apt install` what they downloaded: a manifest-supplied URL would be
  root-level trust in an unauthenticated document (*inference*, REVIEW). So in the
  beta the notice builds its link from a base URL compiled into `release.py` (the
  release page, `…/releases/tag/v{version}`), with `version` only after it has parsed
  as `N.N.N`, so the manifest cannot steer the link anywhere but a release page on the
  project's own host. That is what the surveyed notifiers do: calibre, Cura and Picard
  open their download page (*read*, R-PRIOR). The manifest's `notes`, `channels.*.url` and
  `pypi.commands` are read by nothing in the beta (each channel's instruction is the
  compiled-in sentence of §3.4); they exist for the later applier, which uses them
  only after the signature verifies. **Chosen over** verifying the signature in the
  beta because the scheme and its library are not chosen (below), and a compiled-in
  base needs neither. **Cost:** a forged manifest can still announce a version that
  does not exist; the link then lands on a missing release page, which is a nuisance
  and not a hazard.
- **The signature** is reserved in format 1 and **verified before any
  manifest-supplied URL is used**, which in the beta is never. Electrum's
  feed, signed and checked against pinned keys, is the precedent (*read*, R-PRIOR).
  Which scheme (minisign/Ed25519, or GPG to share the apt key) and whether a crypto
  library already arrives transitively were **not checked**; the stdlib has no Ed25519.
- **Hosting**: a fixed URL such as GitHub Releases' `latest/download/manifest.json`.
  That needs a public repository: a private one's release assets need a token
  (*unchecked*, R-DEB). §6.

### 3.3 One check, at one point

- **When:** once per launch, after the first frame, on a worker thread, with a short
  timeout. Never on the frame path and never before the window. Not on a timer: the
  surveyed apps check daily or at start (calibre daily, kitty every 24 h, *read*), and
  pptmstr sessions are long-lived, but once per launch is the smallest thing that
  meets "stubbed hooks" and adds no clock state. A periodic re-check is a later choice.
- **What it does with the result:** one line in the status bar and in the About/version
  report, naming the version and the channel's own instruction (§3.4). No modal.
- **The no-op:** no marker, an empty `feed`, or the setting off → no request is made
  and nothing is shown. A failed fetch shows nothing on the status bar and the reason
  in the version report: an unreachable feed is not the user's problem to solve mid-work.

### 3.4 The appliers, per channel

| channel | beta applier | later applier |
|---|---|---|
| **deb** | notice: "pptmstr X is available" with the release-page link built from the compiled-in base (§3.2) and `sudo apt install ./pptmstr*.deb` (R-DEB's recommendation) | a signed apt repository, so `apt upgrade` is the applier and the notice becomes informational. Names reserved now so the move is a new package version, not a redesign: `/usr/share/keyrings/pptmstr-archive-keyring.pgp`, `/etc/apt/sources.list.d/pptmstr.sources` (deb822), added behind a debconf question as VS Code's `.deb` does (*read*, code.visualstudio.com/docs/setup/linux; Debian's third-party repository rules, *read*, wiki.debian.org) |
| **pypi** | notice naming `uv tool upgrade pptmstr` / `pipx upgrade pptmstr`. pptmstr never runs them: an app that rewrites its own tool environment while running is the hazard in §3.5 | none planned; this is the channel's convention |
| **mac** | notice with the release-page link built from the compiled-in base (§3.2), not the manifest's `.dmg` URL. **Notice-only is where this channel starts**: R-PY found no off-the-shelf updater fits a lock-built venv | **Sparkle 2, out of process (`sparkle-cli`)**, as the mac doc already proposed for (a). *Inference (PLAN):* R-PY's objection was that Sparkle "replaces a bundle, not a venv"; in (b′) the bundle *is* the unit — interpreter, site-packages and claude binary inside one signed `.app` — so replacing the bundle replaces all four parts at once, which is what the mac doc requires. Velopack also updates a whole directory and produces only AppImage on Linux (*read*, docs.velopack.io). **Velopack is unresolved, not rejected** (REVIEW M5): its docs show only PyInstaller output, and whether it packs a non-PyInstaller directory such as (b′)'s tree was **not read** (R-PY's own least-sure item). A read of Velopack's `packDir` handling settles it |

Anki's step between the two — download the `.dmg` and hand it to `open` (*read*,
`qt/aqt/package.py`) — is available to the mac channel once the signature is verified;
it is not in the beta.

### 3.5 The running process: the unit must not split under it

R-DEB found, by reading, 113 function-level imports in `pptmstr/` (among them
`anthropic`, `imscribe.typesetter`, `imgui_bundle.hello_imgui`, `claude_agent_sdk`).
**2026-10-17, later:** the `anthropic` import is gone. It was removed with the model
probe on 2026-10-17 (task MODEL-STATIC), and so was the dependency. The other three
examples still stand, and the argument below does not depend on the `anthropic` import.
An in-place replacement of the installed files under a running pptmstr therefore lets
the old process import new modules, and the next session it launches runs the new
claude binary, which the SDK finds by `__file__`. The unit splits inside one process
even though the package moved whole.

- **deb — adopted from R-DEB: a versioned payload package and a metapackage.**
  `pptmstr-<version>` installs only `/opt/pptmstr/<version>/`; `pptmstr` installs
  `/usr/bin/pptmstr`, the desktop entry and icons, and the symlink
  `/opt/pptmstr/current → <version>`, and **`Pre-Depends: pptmstr-<version> (=
  <version>)`, not `Depends`** (REVIEW H2, *web*, Debian Policy ch-relationships).
  Policy says `Depends` "takes effect only when a package is to be configured" and
  does not order unpacking; `Pre-Depends` does. With `Depends`, dpkg may unpack the
  metapackage, and so switch `current`, before the new payload tree is unpacked: a
  launch in that window fails, and an upgrade interrupted there leaves `current`
  dangling until it is resumed. A process already running is unaffected either way,
  because the wrapper has already resolved the link (below). With `Pre-Depends`, the new
  tree is in place beside the old one before the metapackage's unpack replaces
  `current` (an atomic rename, *recollection* about dpkg). No `.deb`
  was built, so this ordering is from reading Policy, not from dpkg (REVIEW). The old
  tree stays for running processes and rollback until `apt autoremove` — **except on
  the bare channel, where that may not hold** (REVIEW L3, *recollection*, open):
  `apt install ./a.deb ./b.deb` marks both packages manually installed, so autoremove
  would never remove an old payload, and they would pile up at about 125 MB a release.
  If that holds, old payloads need a bounded cleanup, and the next bullet's
  constraint (the previous tree must outlive a running process) limits where that
  cleanup may run. It is the linux-image
  pattern (*recollection*) and the mac doc's "staged switch with rollback". **Cost:**
  two packages per release, and `apt install ./a.deb ./b.deb` on the bare channel.
  R-DEB's cheaper alternative — one package and a "pptmstr was upgraded; restart"
  guard — does not cover the lazy imports, and is not taken.
- **A hole in that design, and its fix** (*run*, task PROBE-PREFIX, on this machine):
  if the wrapper runs `/opt/pptmstr/current/python/bin/python3`, the running process
  does not stay on its version. Measured with Debian 12's system CPython 3.11.2
  (`/usr/bin/python3.11`, libpython linked in) copied into
  `$TMPDIR/…/v1/python/{bin,lib/python3.11}`, a copy `v2` differing only in a marker
  module, and `current → v1`: invoked through `current/python/bin/python3` (and
  through `…/python3.11`, no leaf symlink), `sys.prefix`, `sys.exec_prefix`,
  `sys.executable` and every `sys.path` entry name `…/current/python/…`, and after
  `current` was atomically switched to `v2` a lazy import of both the marker and
  `json` loaded from `v2`. Invoked through `"$(readlink -f current)/python/bin/python3"`
  (and through the fully resolved binary), the prefix names `…/v1/python` and the same
  lazy imports after the switch loaded from `v1`. So the wrapper resolves once and
  execs the resolved path:
  `exec "$(readlink -f /opt/pptmstr/current)/python/bin/python3" -I -m pptmstr "$@"`,
  and the `.deb` CI job's smoke test asserts `sys.prefix` has no `current` in it.
  **Not measured:** python-build-standalone or any CPython other than Debian's 3.11.2;
  a C extension imported lazily after the switch; the old tree removed under a running
  process — the fix holds only while `<version>/` still exists, so nothing in the
  metapackage's maintainer scripts may remove the previous tree (`apt autoremove`
  under a running pptmstr is the same exposure, *inference*).
- **mac:** Sparkle installs on quit (*recollection*), which avoids the split. The
  beta's notice-only path does not: a user dragging a new `.app` over a running one
  replaces the files under it. The notice should say "quit pptmstr first", and that is
  the extent of the beta's protection. **Open.**
- **pypi:** `uv tool upgrade` under a running pptmstr has the same exposure; the
  notice says "after quitting pptmstr". **Open**, and accepted for this channel.

### 3.6 Where it lives in pptmstr

- **`pptmstr/release.py`**, a functional core per STYLE §1: `channel()` reads the
  marker (or `None`); `parse(text) -> Manifest` (a frozen record; a malformed document
  is a refusal with its reason, not a default); `verdict(running, machine, manifest,
  channel) -> Verdict`, a pure function returning a union —
  `Current | Available(version, instruction) | CannotMove(reason) | Unreachable(reason)`
  — where `instruction` is the channel's sentence from §3.4. The fetch is one small
  shell function beside it; the worker thread and the status-bar line are the app's.
- **The setting:** one field in `Settings` (`pptmstr/settings.py`), the one settings
  place, `check_for_releases: bool = True`, shown as a checkbox (the settings record:
  "every control is a checkbox or a list") on a page with the channel and the feed
  URL as read-only rows. The feed URL is **not** a setting: it is the packager's fact,
  in the marker. `tests/test_settings_dialog.py::test_every_persisted_field_has_one_place_in_the_dialog`
  will fail until the field has its row, which is the point of that test.
- **Relation to the pins and lock:** `release.verdict` reads the running unit from
  `pairing` (the pins) and `pptmstr.__version__`; the manifest's `unit` is generated
  in CI from the same `pairing` constants, and a test holds them together. The lock is
  what the `.deb` and `.app` are *built from*; the seam never reads it.
- **`pptmstr --version`** gains one line: the channel (or "checkout"), and the last
  verdict if one was reached this launch.

### 3.7 Why design and not code this round

The lead's decision, recorded: this round produces the seam's design, not code. A
stub with no consumer — a `release.py` that no packaging writes a marker for and no
feed serves — would precede the vehicle that calls it, and a watchdog nothing calls
reads as covered when it is not (STYLE §2, "Test the wiring, not only the unit").
The first buildable step (§7) adds it together with the first vehicle that writes a
marker.

## 4. CI on GitHub Actions

Runner labels as R-PY read them on 2026-10-17 (*read*,
docs.github.com/en/actions/reference/runners/github-hosted-runners and
github.com/actions/runner-images): Linux `ubuntu-latest` (= 24.04), `ubuntu-22.04`,
`ubuntu-26.04`, arm variants; macOS arm64 `macos-latest` (= macOS 26), `macos-26`,
`macos-15`, `macos-14` (**deprecated 2026-07-06, retired 2026-11-02, October
brownouts**, *web*, REVIEW, actions/runner-images#13518); Intel `macos-15-intel`, `macos-26-intel`;
Windows `windows-latest` (= Server 2025), `windows-2025`, `windows-2022`,
`windows-11-arm`. Public repositories run standard runners free; private ones pay per
minute, macOS about ten times Linux (*read*, GitHub billing docs).

| job | runner | when | does |
|---|---|---|---|
| **test-unlocked**, **test-locked** (mac doc §7.5) | Linux x86-64 in a glibc 2.28 container; `macos-15` | push, weekly | §7.5 as written, CPython 3.11 and 3.14. **Changed:** §7.5 says "macOS 14 arm64"; that image retires 2026-11-02 and browns out before then. Run on `macos-15` and keep building for 14 (`MACOSX_DEPLOYMENT_TARGET=14.0`, as `make lock` already sets). Picard builds on `macos-15` for a 13.0 target (*read*, R-PRIOR P6), so building newer-for-older is the norm. What this loses: nothing hosted *tests* on macOS 14 after the image retires. §6 |
| **lock-check** | `ubuntu-latest` | push | `make lock-check`, offline |
| **wheel** | `ubuntu-latest` | tag | write the `pypi` marker; `uv build`; upload the dist as an artifact |
| **publish-pypi** | `ubuntu-latest` | tag, after **wheel** | a separate job in a `pypi` environment with `permissions: id-token: write`; `pypa/gh-action-pypi-publish` pinned to a SHA; PEP 740 attestations are on by default since v1.11.0; **not** from a reusable workflow, which trusted publishing does not support (*read*, github.com/pypa/gh-action-pypi-publish and docs.pypi.org/trusted-publishers) |
| **deb** | `ubuntu-latest` | tag (and weekly, unsigned) | fetch python-build-standalone x86_64 `install_only`; install the lock with `--require-hashes --only-binary :all: --no-deps`, then the wheel `--no-deps`; byte-compile under `SOURCE_DATE_EPOCH`; write the `deb` marker; `dpkg-deb --root-owner-group -Zxz` for both packages (§3.5); the verification below; then install into `debian:11` and `ubuntu:24.04` containers and run `pptmstr --version` (no display). **This does not test containment** (REVIEW M4): the probe is not in the `.deb`, and Ubuntu 24.04's AppArmor restriction comes from the host kernel, so a container on a CI host shows the host's state, not Ubuntu's; Docker's default seccomp may also block bwrap's namespaces in a container (*recollection*, REVIEW). The in-app AppArmor check (§7) is tested by unit tests over given sysctl values, and on a real Ubuntu 24.04 machine only by a person |
| **mac** | `macos-15` | tag | the same tree with the aarch64-apple-darwin build; assemble the `.app`; sign every Mach-O with hardened runtime from a temporary keychain; `xcrun notarytool submit --key … --wait`; `xcrun stapler staple`; `.dmg` (keychain steps *read*, docs.github.com sign-xcode-applications; notarytool with an API key *search*, Apple forums). Without an Apple identity (§6) it produces an unsigned `.app` for internal testing only |
| **release** | `ubuntu-latest` | tag, after all | write `manifest.json` from the jobs' hashes and `pairing`'s pins; sign it; attach everything to the GitHub release |

**Packaging verification, in the deb and mac jobs:**

- **The bundled claude is byte-for-byte the wheel's.** Compare its sha256 with the
  `claude-agent-sdk` wheel's `RECORD` entry. *Proposed here (PLAN):* this does not
  depend on any output format of the CLI and catches strip, re-signing that rewrites
  more than a signature, or any other rewrite. On macOS, re-signing changes the file,
  so there the check is the second bullet only, run after signing — unless the binary
  is already Developer-ID signed and is not re-signed (§2.3, open).
- **The probe's agent-CLI check passes** on the staged tree. PROBE-CLI (complete,
  **uncommitted** in this worktree: `scripts/probe.py`, `tests/test_probe.py`) now
  refuses a CLI whose `--version` lacks `(Claude Code)`, which is what tells a stripped
  binary (Bun's `1.4.3`) from a real one (*run* by builder-4, CLI 2.1.251–2.1.301 on
  Linux x86-64). **Risk:** that suffix is **not documented** by Anthropic; it is what
  every release checked happened to print. If a release changes its `--version`
  line, this check fails closed — a red on a good binary, not a green on a bad one —
  and the RECORD hash above is the check that does not depend on it.
- `desktop-file-validate` on the desktop entry; `sys.prefix` with no `current` in it
  (§3.5); the marker present and naming the right channel.

No Windows job. If Windows returns, `windows-2025` and `windows-11-arm` exist; the SDK
has no win_arm64 wheel (*read*, R-WIN).

## 5. The three questions

**5.1 Is NSIS still widely accepted for Windows, or are there more modern options?**
NSIS is alive: 3.13, 2026-09-27 (*read*, nsis.sourceforge.io). The alternatives as of
now (R-WIN): Inno Setup 7.1.0, whose authors **ask** commercial users (over US$5k
revenue) to buy a licence; **it is not required** (*web*, REVIEW: jrsoftware.org
isorder.php, "It is not strictly required"; isorder-terms.php §8, "you are not
expected to purchase a commercial license"; files/is/license.txt permits commercial
use). An earlier version of this record said a paid licence was required; that
error came in through R-WIN, which read only isdl.php's "Please purchase a commercial
license or donate". WiX v6/v7 for MSI, under the Open Source Maintenance Fee:
organisations with "more than $10,000 in annual revenue" must sponsor; v6 is under
OSMF v1.0, v7 under v1.1 with EULA acceptance enforced (*web*, REVIEW,
docs.firegiant.com/wix/osmf); MSIX, which installs
only when signed with a certificate the machine trusts (*relayed*, search); Velopack,
installer and updater in one, Python "Ready", per-user install that swaps the whole
app folder on update (*read*, docs.velopack.io). For an app whose update is "replace
an interpreter, site-packages and a 250 MB claude.exe together", Velopack is the only
one built around that; NSIS, Inno and WiX would need an updater added. Signing: EV
certificates no longer buy instant SmartScreen reputation (*relayed*), and Azure
Artifact Signing reached GA in January 2026 at $9.99/month, individuals eligible in
the US and Canada (*relayed*, devclass.com; confirm on Microsoft's page). **None of
this is needed for the beta** (§1.4).

**5.2 Are Python venvs still easily packaged these days?** Easily *recreated* from a
lock, yes — that is what `uv tool install` and bootstrap do. Easily *shipped*, no: a
venv is not relocatable (absolute shebangs, `home` in `pyvenv.cfg`; *read*,
docs.python.org), and `uv venv --relocatable` guarantees only entry-point scripts
(*read*, docs.astral.sh). What ships is a portable interpreter with packages installed
into it (§2.3). Every freezer (PyInstaller, Nuitka, py2app, and by recollection
Briefcase) makes `sys.executable` not a Python, which pptmstr's import gate needs
(R-PY H2).

**5.3 Is Windows a supported target for GitHub Actions?** Yes: `windows-2022`,
`windows-2025` (= `windows-latest`), `windows-11-arm` and `windows-11-vs2026-arm`
(*read*, GitHub's runner docs). Free on public repositories; $0.010/min on private,
against Linux's $0.006 (*read*, GitHub billing).

## 6. Open, the user's to decide

Each with a recommendation. None of these is an agent's to settle.

1. **Apple Developer Program membership and whose identity signs.** Required for a
   notarised `.app` (§2.3). *Recommend:* enrol under the organisation (holond), not a
   personal account: Anki had to create a new Developer ID because the old one was
   tied to a founder's personal account (*read*, R-PRIOR). The fee is *recollection*
   and was not read.
2. **Whether Anthropic's terms allow a third party to redistribute the claude
   binary.** The `.deb` and `.app` both carry it; PyPI does not. The SDK wheel is MIT,
   but the CLI's own LICENSE reads "© Anthropic PBC. All rights reserved", use under
   Anthropic's commercial terms (*read*, R-FLATPAK); the terms themselves were **not
   read**. *Recommend:* read the commercial terms, and ask Anthropic, before any
   public `.deb` or `.dmg`. If the answer is no, the installers ship without it and
   point the SDK at a user-installed `claude`, which costs first-launch setup and a
   CLI version pptmstr was not tested against — the Anki complaint in another form.
3. **Whether the GitHub repository is public.** The feed, the bare `.deb` and the
   `.dmg` on Releases need it to be, or another host (*unchecked*, R-DEB). It also
   decides whether CI minutes cost anything.
4. **Publish a first PyPI version early to hold the name.** `pptmstr` is free today,
   and a pending trusted publisher does not reserve it (*read*). *Recommend:* yes,
   once the `requires-python` cap (§1.3) is in, with a description that says macOS
   and Linux only. **The name-holding version must carry the cap too**: a resolver
   that meets a cap backtracks to the newest version without one (*web*, REVIEW,
   uv's resolution docs), so an uncapped first release would become what every 3.15
   install resolves to (§1.3).
5. **The macOS floor after `macos-14` retires** on 2026-11-02, with brownouts
   through October 2026 (*web*, REVIEW, actions/runner-images#13518). *Recommend:* keep building for 14
   on `macos-15` and state the claim as "built for macOS 14, tested on 15 and later"
   until a person or a self-hosted machine runs 14. Cura's macOS builds are
   self-hosted (*read*, R-PRIOR); Anki still uses `macos-14` (*read*).
6. **An apt repository, its hosting and its signing key** — after the beta. A key's
   custody is the operator's: an offline primary and a CI signing subkey, an expiry
   and a rotation plan (R-DEB). GitHub blocks files over 100 MiB and Pages sites are
   1 GB (*read*, GitHub docs), so a ~125 MB `.deb` cannot live in a Pages repo; the
   pool goes to release assets or object storage with aptly (*read*, aptly.info).
   Kolibri runs its own apt repo on GitHub Pages after Launchpad failed Debian 13's
   key constraints (*read*, kolibri.readthedocs.io) — so whatever key is made should
   meet Debian 13's requirements, which were **not read** here. *Recommend:* the bare
   `.deb` and the notice for the beta; decide the repo when there are users to upgrade.
7. **Ubuntu 24.04+ and AppArmor.** *Recommend:* the beta says the sentence and the
   documented remedy, from the app (§1.2, §7 step 5); a shipped profile, a system-wide grant, only as an opt-in
   debconf question later (§1.2).
8. **macOS settings location** (mac doc §4): `~/.config/pptmstr` as today, or
   `~/Library/Application Support/pptmstr` with a migration. *Recommend:* decide
   before the first `.dmg`, since moving after users exist is a migration.
9. **Bundle and desktop identifiers:** `dev.holond.pptmstr`, as R-DEB proposed from
   the owned domain. *Recommend:* yes; the identifier is hard to change after release.
10. **AppImage** came up as R-FLATPAK's alternative and was **not researched**.
    *Recommend:* leave it unless the `.deb` proves too narrow (non-Debian distros).

## 7. The first buildable step

**The PyPI channel, with the seam's first consumer.** It is the one channel not
blocked on the lock, an Apple account or the redistribution question, and it is the
only one that holds the name.

1. Cap `requires-python` at `<3.15` (with a test holding it to
   `pairing.IMTYPESET_PYTHONS`), and set classifiers to macOS and Linux.
2. `pptmstr/release.py` as §3.6, with `pypi` as its first real channel: the marker
   written by the wheel job, the pure `verdict`, and tests over it (`Current`,
   `Available`, `CannotMove` when `requires.python` excludes the running minor,
   `Unreachable`, a malformed manifest refused). The feed stays empty in the marker,
   so the shipped behaviour is the no-op until a manifest is published.
3. `Settings.check_for_releases` and its dialog row; the `--version` channel line.
4. The **wheel** and **publish-pypi** jobs (§4), first against TestPyPI.
5. **The containment check in the app** (§1.2, REVIEW M4). On Linux, before a
   contained rung starts, read `/proc/sys/kernel/apparmor_restrict_unprivileged_userns`;
   if it is `1` and bwrap cannot create a user namespace, refuse the contained rungs
   with the sentence and the documented remedy instead of leaving it to
   `failIfUnavailable`'s failure. A pure verdict over the sysctl value and a namespace
   attempt's result, with tests over each combination; the reading is the shell. It
   belongs in this first step because a PyPI install on Ubuntu 24.04 meets the same
   restriction. *Inference (PLAN-FIX):* the exact remedy text is Claude Code's
   sandboxing page's, which REVIEW confirmed matches (*web*); the check itself has
   never been run on an Ubuntu 24.04 machine, and whether `failIfUnavailable` exits
   at start or refuses each Bash call there was not run (§1.5).

Then, once `make lock` has written the lock (mac doc §7.6): the **deb** job and the
versioned package pair, which is the first channel whose marker carries a feed; then
the **mac** job, once item 1 of §6 is settled.

## 8. Not run, and least certain

- **Nothing in this record was run by its writer.** The measurements it cites were
  run by R-DEB's builder (strip, readelf, dpkg-deb, desktop-file-validate, on Debian
  12), PROBE-CLI's builder (the CLI's `--version` marker, the strip reproduction),
  PROBE-PREFIX's builder (§3.5's directory-symlink measurement, CPython 3.11.2) and
  the operator (the Flatpak namespace test).
- Not built by anyone: a `.deb` of pptmstr, a `.app`, a wheel, a python-build-standalone
  tree with the lock installed into it (no lock exists, and no network here), Sparkle,
  any CI job.
- REVIEW (reviewer-3) ran nothing either: its corrections folded in here are *web*
  reads through a summarising fetch, reads of the tree, recollection or inference, as
  each is marked. In particular §3.5's `Pre-Depends` ordering is from Policy, not
  dpkg; L3 (apt's manual marking) and L4 (the claude binary's existing signature) are
  recollection and open; whether `uv tool install` refuses a capped release on 3.15 is
  open.
- **Least certain:** whether §3.5's directory-symlink measurement, made on Debian's
  CPython 3.11.2, holds for the python-build-standalone build the `.deb` would ship
  (the `.deb` CI smoke test is where that gets checked); and whether Briefcase
  keeps a real interpreter (§2.4), which would change §2's vehicle.

## 9. Sources

The five research results on the board, each with its own URLs and dates
(2026-10-17): R-PY (python.org venv; docs.astral.sh/uv; pyinstaller.org; briefcase.beeware.org;
nuitka.net; py2app; ofek/pyapp; astral-sh/python-build-standalone; conda/constructor;
sparkle-project.org; docs.velopack.io; dennisvang/tufup; winsparkle.org; GitHub runner,
billing and signing docs; docs.pypi.org; pypa/gh-action-pypi-publish; pip; pipx).
R-WIN (PyPI JSON for imtypeset, imscribe, imgui-bundle, claude-agent-sdk;
code.claude.com/docs/en/sandboxing; NSIS; Inno Setup; WiX/FireGiant; Velopack;
Microsoft sideloading; devclass.com). R-FLATPAK (flatpak-run.c; the portal XML;
docs.flathub.org requirements and linter; flathub/com.visualstudio.code;
flatpak-builder-tools; claude-code LICENSE.md). R-DEB (spotify/dh-virtualenv;
packages.debian.org; launchpad; gregoryszorc.com python-build-standalone; nfpm;
lintian; wiki.debian.org/DebianRepository/UseThirdParty; code.visualstudio.com Linux
setup; GitHub large-file and Pages limits; aptly; freedesktop desktop-entry spec;
discourse.ubuntu.com). R-PRIOR (Anki forums, release.yml and `qt/aqt`; calibre and
kitty bypy; Thonny packaging/mac; Spyder installers-conda and updatemanager;
Cura-workflows; Electrum; Picard; Tribler; Kolibri; mu-editor/pup). In the tree:
`pyproject.toml`, `pptmstr/pairing.py`, `pptmstr/settings.py`, `pptmstr/__main__.py`,
`pptmstr/commandrun.py`'s module docstring, and the uncommitted diff of
`scripts/probe.py`. REVIEW (reviewer-3, 2026-10-17): docs.flathub.org requirements;
code.claude.com/docs/en/sandboxing; jrsoftware.org isdl.php, isorder.php,
isorder-terms.php and files/is/license.txt; docs.firegiant.com/wix/osmf;
actions/runner-images and issue #13518; docs.github.com github-hosted-runners;
pypa/gh-action-pypi-publish; docs.astral.sh/uv/concepts/resolution; Debian Policy
ch-relationships.
