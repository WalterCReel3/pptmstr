# The toolkit was never the constraint

**Dated:** 2026-09-08 · **Status:** decision recorded; the port question is closed,
the text-widget direction is open and unscoped ·
**Bears on:** [`../notes/2026-08-31-the-instrument-is-the-product.md`](../notes/2026-08-31-the-instrument-is-the-product.md)
§6.5.3, whose thesis argues the operator surface should shrink ·
**Corrects:** [`archive/2026-08-16-the-wake-up-crash-belongs-to-glfw.md`](archive/2026-08-16-the-wake-up-crash-belongs-to-glfw.md)
— see §7

**The question was whether to convert this application to an Electron app with a Rust
sidecar. It widened through Qt, remote access, audience growth, and patching Dear
ImGui upstream. The answer to all of it is that the render layer is not what is
holding this application back, and every candidate replacement was assessed against a
constraint that does not exist.**

Nothing here is built. One direction is left open and deliberately unscoped.

---

## 1. The Rust sidecar is not viable, and the reason is not performance

The Agent SDK ships for Python and TypeScript only; the documented cross-language
path is `-p` with `--output-format json`, which is the one-shot form `driver.py`'s
module docstring already rejects because it cannot reach `get_context_usage`,
`interrupt` or `set_permission_mode`.

The escape hatch — the SDK is a subprocess wrapper, so any language can drive it — is
half true and the failing half is the half this application lives on. The bundled CLI
multiplexes two channels on one stdout pipe. The documented one carries assistant
messages and results. The undocumented one carries `control_request` /
`control_response` frames with subtypes `can_use_tool`, `hook_callback` and
`mcp_message` — which is to say the approval gate *is* a `PreToolUse` hook callback
and the agent bus *is* an in-process MCP server. A Rust sidecar would be
reimplementing an unpublished protocol and re-paying that cost at every CLI release.

There is also nothing for it to do. No CPU-bound work exists anywhere in the tree; the
timing constants are human- and turn-scale; the pool waits on subprocesses. And
`driver.py` only looks like a sidecar because it is the largest module — roughly 5% of
it is SDK API surface, ~16% message translation, and the remaining ~75% is
orchestration policy that is not faster in another language.

If a second process is ever wanted, TypeScript has a first-party SDK. Rust does not.

## 2. A port is affordable. That was never the reason not to do it

Five rounds compared candidate toolkits against the status quo. That framing gave the
incumbent a permanent advantage: a null hypothesis that only has to *not lose*
accumulates one. A sixth round inverted it — three agents were told the decision to
port was made and instructed that arguing against it was task failure — and produced a
costed migration rather than a comparison.

Front end ~4,200–6,000 lines, Python serialiser and server ~400–800, TypeScript tests
~3,400–6,900 at this repository's own 1.145:1 test-to-product ratio. **Total roughly
8,000–13,700 lines, against the 39,089 of product and test built here in about four
weeks.** A quarter to a third of the original build. Two caveats keep that from being
a clean number: the 39k was Python, and it was greenfield with discovered
requirements rather than a reimplementation obliged to keep matching a live system.

Three facts fell out of that round that five rounds of comparison never surfaced, and
they are worth keeping regardless of what happens next:

- **The UI never writes to the store.** Zero `store.apply` under `pptmstr/ui/`; exactly
  one in `app.py`, at `begin_frame`. Every operator mutation already goes out through
  `Bridge.emit` / `resolve` / `submit`, all three safe from any thread by
  construction. The complete operator command surface is ten calls, and two panes
  already declare it as a value type (`inbox.InboxActions`, `health.HealthActions`).
- **Change notification would need zero lines in `store.py`.** `_apply` shallow-copies
  each map and assigns only the touched key, and records are frozen and replaced via
  `dataclasses.replace` — so `prev.nodes.get(k) is not cur.nodes[k]` is a sound
  conservative change test. A server-side diff needs no observer, no dirty flag, and
  no compromise of the reducer's purity. `Snapshot.seq` would finally have a consumer.
- **The Snapshot boundary is smaller than it looked.** Twenty of `AgentRecord`'s
  twenty-one fields serialise directly; `transcript` becomes an integer length and
  moves to a separate pull-shaped byte channel that needs no backpressure, because
  nothing is queued — the server holds one integer per subscription.

**A contradiction the round did not resolve, flagged rather than settled.** The plan's
migration sequence rests on running both front ends against one `Store`. The
write-side argument for that is sound and verified. It is also the wrong side:
coexistence is blocked by *reads*, and `Store.snapshot()` forbids it in its own words
— *"Call exactly once per frame (§4.1) -- calling it twice is a bug even when it looks
harmless, because the two results can differ and the frame then renders torn state."*
Two readers do not corrupt anything, but their views disagree by up to a frame, which
destroys the value of side-by-side comparison — and that comparison was the entire
justification for the coexistence step. Anyone reviving this must resolve that before
trusting the sequence.

## 3. Qt, Electron and the webviews, briefly

Ranked against each other rather than against staying, the order is not the intuitive
one.

Qt's real advantage is that it calls the existing core in-process while every web
option must serialise. Against that: it makes the Linux install wall *worse* — Qt 6's
xcb plugin needs roughly twenty system libraries where the vendored GLFW needs seven,
and `libxcb-cursor0` is the most-reported Qt 6 first-run failure; its LTS is a
commercial product, with open-source users getting roughly six months of patches on
any release; its markdown parser exposes no rule hooks, so `inline.py`'s
underscore-emphasis suppression is unrecoverable without abandoning Qt's markdown
path; and `QAccessible` is free only for standard widgets, not custom-drawn panes.

Electron wins exactly one axis, and only if the audience widens: the cost of a defect
the maintainer cannot reproduce. Today a wake crash produces no Python traceback at
all, because Xlib prints and calls `exit(1)` from C. Chromium normalises the platform
and hands a remote user a copy-pasteable stack. That is support economics, not text
layout — and text layout is where every other argument was spent.

Tauri is worse than the status quo on Linux reach: versioned `webkit2gtk`, and three
webview engines to test instead of one.

**And the shell choice is not downstream packaging, contrary to how it was framed.**
At least four things leak above the boundary, one a correctness defect: complete find
requires intercepting Ctrl+F, which a plain browser tab cannot reliably do; the
launcher's cwd has no filesystem-path API in a plain tab; a dock badge for `needs_you`
needs app identity; and **closing a browser tab does not run `app.py`'s `finally:`**,
which reintroduces the parked-approval loss described in §5.

## 4. Patching Dear ImGui upstream is an empty set

Of the five text weaknesses, two are refused by design and three need no C++.

The refusal is explicit and verbatim at the installed tag, `v1.92.8/docs/README.md`:
*"full internationalization (right-to-left text, bidirectional text, text shaping
etc.) and accessibility features are not supported."* No effort level opens those. On
accessibility specifically, ocornut declines an 80% solution he cannot take to 99% and
recommends a fork — the strongest form of no, because effort will not move it.

The other three are already ours. `rich_pane` does not use ImGui's text layout: it
draws its own runs with `dl.add_text` at coordinates `span_layout.layout_inline`
computed. Selection over that is a hit-test, a rect fill and a clipboard write, in
Python. `rich_pane.windowed()` already computes a per-block cost model. And font
fallback is `ImFontConfig::MergeMode`, which predates 1.92 and which `theme.load_fonts`
already uses for FontAwesome — the tofu in `DISCLOSURE_GLYPH`'s comment is the chain
working as designed with no source in the list carrying U+25B0, not ImGui failing to
fall back.

Upstream acceptance would not save it anyway. `ocornut/imgui#8833` — a competent
fallback PR from a known contributor — has been open since July 2025. `#4227` has had
nothing from the maintainer in four and a half years.

**And the layer with the escape hatch is not the layer with the problems.** Dear ImGui
and hello_imgui are statically linked into a single 27 MB extension (`NEEDED
libglfw.so.3`, `RPATH [$ORIGIN/./:$ORIGIN/../imgui_bundle.libs]`, verified with
`readelf`). GLFW ships as a separate 306 KB object. An ImGui patch means owning a
29-wheel CI matrix and a permanent rebase; a GLFW patch means replacing one file,
which any `pip install -U` silently reverts.

Note for anyone who reads this and reaches for a Rust harness: the clean-room
reimplementation assessed in
[`archive/2026-08-14-the-transcript-outlives-the-window-and-our-record-of-it-does-not.md`](archive/2026-08-14-the-transcript-outlives-the-window-and-our-record-of-it-does-not.md)
is a rebuild of source that leaked. Using it as a basis is a licensing and provenance
decision for an MIT project, not an engineering one.

## 5. What is actually holding this back, and none of it is the toolkit

Ranked, from an audit of what a wider audience would hit:

1. **There is nothing to install.** No release, no `[project.urls]`, no CI, no
   installer on any platform. `[project.scripts]` and the hatchling sdist config
   already exist. This is upstream of everything else on the list and is the cheapest
   item on it.
2. **Conceptual load, and `templates.BUILT_IN` is a hardcoded tuple.** A user cannot
   add a role or a team without editing Python, in an application whose value
   proposition is team shape. `templates.py`'s own docstring says a template is *"the
   kind of thing that eventually comes from a file the operator edits"* — it is not
   one yet.
3. **The approval gate is the workflow.** Six-hour park, no policy dial, `declare_task`
   parking per declaration. This is thesis-level and
   [`../notes/2026-08-31-the-instrument-is-the-product.md`](../notes/2026-08-31-the-instrument-is-the-product.md)
   §6.2 is harsher about it than anything found here.
4. **Auth is never checked.** `probe.py`'s `check_agent_cli` runs `claude --version`,
   which succeeds when logged out. The first launch then dies in `driver.py`'s broad
   `except Exception` and surfaces as a raw SDK exception type in a FAILURE
   obligation. An engineer reads that; a new user reads "broken."

Items 1, 2 and 4 are toolkit-irrelevant and cheap. Item 3 is a product thesis.

**The one that matters most is not on that list, because it is not about the
audience.** Parked approvals are lost on any abnormal exit, and the GLFW crash is one
trigger among several — SIGKILL, OOM, power loss, and the panel-exception path
`guarded()` exists for all defeat a `finally:` equally. `Bridge.stop`'s own docstring
says rejecting parked approvals only *schedules* it, so the clean path loses them too.
There is no approval persistence anywhere in `pptmstr/`, while the atomic idiom is
written three times already — `settings.py`, `sessions.py`, `brief.py`, all
`mkstemp` + `os.replace`. **The cause fix is a fourth use of that pattern.** Patching
GLFW is the symptom fix and was mistaken for the cause fix during this session.

## 6. The direction that is left open

Build a stronger text widget in `pptmstr/ui/`, in Python, against this application's
real workload, before deciding whether it is ever extracted.

The justification is stronger than "add selection." Three render modes exist with
three capability sets — RAW virtualises but cannot wrap, WRAP wraps but caps at 400
lines silently, RICH renders markdown and also caps — because no single ImGui path
does wrap, virtualise and colour together. A widget that owns its own layout collapses
that split, which is also the design constraint: it must own the height model.

Two scoping tasks converged independently on the same finding: **`Run` is the wrong
shape.** It carries text and four style booleans, and it discards both the source
offset (needed for selection) and the width it just computed (which `_draw_rows` then
recomputes). It should carry position and extent.

What is known about the cost:

- Moving WRAP off `imgui.text_wrapped` onto the self-drawn path is small — six code
  lines out, ~20 in, reusing 79 lines of `rich_pane` machinery that already ships.
  `layout_inline` does not presuppose `blocks.py`; `tests/test_span_layout.py` already
  drives it with hand-built `InlineToken`s. But the swap alone buys only run
  rectangles.
- Copy of the *rendered* text is ~20 lines plus one bool on `Row`. Copy of *source*
  markdown from a partial selection is not, and the wall is markdown-it: `map` is
  never set on inline tokens, `markup` was dropped in the hashable flattening, and
  `text_join` merges adjacent text tokens so an escaped `\*` is indistinguishable from
  a literal one. Token content is not a substring of the source for escapes or inline
  code, so offsets cannot be recovered by scanning.
- Lifting the 400-line cap does not need the self-drawn path at all. `calc_text_size`
  takes a `wrap_width`, and the five-argument `ImDrawList.add_text` overload takes one
  too, so wrapped height and wrapped drawing are each one call with wrapping in C++.
  Scrollback and selection are separable and can ship on different timelines.

**Nothing here is declared as work.** The measurement that would scope it has not been
taken: `transcript_pane.py:243` slices `cache.lines[:stable]` every frame — O(total
lines), unbounded, at frame rate — while the parse inside `feed()` is O(new lines).
One profile at 100k lines settles whether the incremental parser is what makes RICH
affordable, and therefore whether preserving it matters. Take the measurement first.

**Before touching any of it:** nothing in `tests/` covers `_draw_lines`, `RenderMode`
or the WRAP path. `scripts/verify_transcript_copy.py`, a live script driving synthetic
mouse input, is the entire safety net for the code a widget would replace.

## 7. Corrections to the record

**The wake-crash record cites the wrong PR, and it is the load-bearing sentence.**
[`archive/2026-08-16-the-wake-up-crash-belongs-to-glfw.md`](archive/2026-08-16-the-wake-up-crash-belongs-to-glfw.md)
says *"There is no released fix to upgrade into: glfw#1147 added `if (!ci) continue;`
to the polling loop only."* glfw#1147 is "X11: Expose zero monitor for functional
headless", elmindreda, 2017-11-28, and its entire diff is `if (!_glfw.monitorCount)` →
`else`. It contains no NULL guard. The guard in the polling loop came from **glfw#2766**,
opened by a Fyne maintainer 2025-09-06 and merged 2025-11-07 as `4df5129` — note the
GitHub API reports `merged: false` because dougbinks rebased it by hand.

**The record's conclusion survives, verified against the 3.5.1 tag.** The merged fix
does not reach us for two independent reasons: it guards `_glfwPollMonitorsX11`, while
the fatal request comes from the per-frame `_glfwGetMonitorWorkareaX11`; and a NULL
guard is inert under the default Xlib handler, which exits rather than returning NULL.
The record's "obvious mitigation is a trap" reasoning is intact and was re-verified.

What changes is the *maturity* inference drawn from it elsewhere. GLFW is not an
abandoned upstream: 3.5.1 shipped 2026-07-31, master carries a "Start 3.6" commit, and
merged external PRs run 50–90 days. The obstacle is narrower and more interesting —
upstream believes this defect is fixed, so a new patch must argue against a closed
issue. Separately, imgui_bundle pins its glfw submodule at `7b6aead9`, "Documentation
updates for 3.4 release", 2024-02-23, and has not moved it in 2.5 years; `.gitmodules`
says `branch = 3.3-stable`, which contradicts the gitlink and will mislead anyone
checking. So "upstream and wait" is gated on a submodule bump, not on GLFW.

**Smaller corrections found in passing, none of them boarded as work:**

- `pyproject.toml`'s dev-deps comment argues that an unpinned formatter behind a
  `--check` gate is a latent failure, and attaches it to `ruff format`. `make lint`
  runs `ruff check` then `black --check`; `ruff format` is never invoked, and
  `[tool.ruff]`'s own comment says so. The tool actually gating is black, floored at
  `>=24.1`, spanning three stable-style years.
- `review.SHORTCUTS` carries the comment *"Kept in one place because the help line and
  the handler must not drift apart"* and has exactly one occurrence in the tree: its
  own definition. What renders is a hardcoded f-string in `inbox.draw` omitting
  `Shift+A` and `esc`. The drift it exists to prevent has happened.
- WRAP's `lines[-_WRAP_WINDOW:]` is the only bound in the application that does not
  announce itself, against the rule stated in
  [`2026-08-10-layout-proposals.md`](2026-08-10-layout-proposals.md).
- `transcript_pane._visible()` filters the whole line cache every frame with no memo
  and no debounce, contradicting the pane docstring's own cost argument.
- `ConcernEdited`, `ConcernWithdrawn`, `AgentRemoved` and `pool.set_cap` have reducer
  arms or methods and no product producer.
- `theme.py` merges FontAwesome into BODY and not into BOLD, while `rich_pane`
  measures in the ambient font and draws bold runs in `Face.BOLD`. Latent, not live:
  it holds only because `Fonts[0]` is BODY and no caller pushes a face. A widget
  called from arbitrary panes cannot rely on that.

## 8. Method, and what it is worth

Fourteen agents over seven rounds. **Almost none of it is run-derived.** The agents had
no shell; every number they produced is a file read, a hand attribution, or a web
fetch through a summarising layer that was caught fabricating GitHub issue numbers
once in this session and returning wrong release dates once.

That matters because of what happened when one hand-estimate was finally audited. The
executable-line residue of `pptmstr/ui/` had been estimated at 2,000–2,500 from a
sample of two files and used as an input by two downstream rounds. Counted by AST:
**7,523 total, 1,173 blank, 1,087 comment, 1,871 docstring, 3,392 executable** — the
estimate was low by about 40%, in the optimistic direction. A second agent counting by
a different method got 3,383 and correctly predicted its own bias direction.

The rest of the session's arithmetic rests on the same kind of attribution and has not
been audited. Two commands would close most of it: `pytest --cov=pptmstr/ui` for the
test-loss figure, and deleting the twenty `monkeypatch.setattr(..., "imgui", ...)`
sites to see what actually fails. Treat every number in this record accordingly, except
the ones marked as counted here and the ELF facts in §4.

One thing was verified and is worth keeping: `pytest --collect-only` reports **1,282
tests in 0.85s with no display**. The whole suite is genuinely headless.
