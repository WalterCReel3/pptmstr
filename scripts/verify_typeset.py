#!/usr/bin/env python3
"""
Does the typeset seam work in a real window, through the app's own font path?

``pptmstr/ui/typeset.py`` is unit-tested in bare ImGui frames with no backend
(``tests/test_typeset.py``). Three things that cannot show there are what this is for:

  1. **Fonts through ``theme.load_fonts``.** The real callback, under the real runner:
     Inconsolata is still the default font afterwards, every one of the library's
     faces loaded through ``hello_imgui``'s loader, and nothing is left pending to be
     read mid-session.
  2. **A frame with a renderer.** A markdown document, two diffs, a stream, literal,
     wrapped and whitespace-exact text and a document of parts through the seam, each
     reporting glyphs drawn, none without a raster when it was drawn and none
     replaced by U+FFFD when it was built. Those are two different counts
     (``ViewCache.drawn`` and ``ViewCache.replaced``), and one view here is there to
     show it: text no shipped face can set, which draws four glyphs with none
     missing and two of them not what was written.
  3. **The switches, live.** Monospace prose and a palette change applied between
     frames: the first rebuilds the documents, the second (light to high contrast,
     nothing else changing) must not.

It also drags a selection in the markdown view and then in the stream, with events
posted to ``ImGuiIO``, and checks that the first view gave the selection up: one
selection at a time is the library's promise and this is the only place it is seen
across views built by the seam. The events enter at ImGui, not at the X server.

A PNG is written at each stage for a person or a reviewer to open; the checks say
nothing about how any of it looks.

Usage:  scripts/xvfb_shot.sh -- .venv/bin/python scripts/verify_typeset.py
        .venv/bin/python scripts/verify_typeset.py          # with a display
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

MARKDOWN = """\
# The seam, in one document

Every pane that sets text goes through **one module**, and this paragraph is
hard-wrapped near eighty columns the way a model writes it, with *emphasis*,
`inline_code`, an identifier like `__init__` and a bare __init__ and foo_bar_baz,
a shell line with $PATH=$HOME/bin, an arrow → and a tick ✓, and a
[link](https://example.org/seam).

## What raw HTML does

A sentence naming `Vec<T>` in code and List<String> outside it, then a block:

<div class="note">this line is HTML and must still be shown</div>

### Lists

1. First, with a nested list:
   - one level down
   - and another item that is long enough to wrap onto a second line at this width
2. Second.

> A block quote takes the dim role, and its bar is the border colour.

```python
def approval_diff(pending):
    return tool_diff(pending.tool_name, pending.raw_args)  # x < y
```

| role | pptmstr palette | note |
|---|---|---|
| heading | `accent` | every level |
| code | `diff_context` | the colour the old renderer gave a fence, in a long cell that wraps |

---

Last paragraph.
"""

BEFORE = "def greet(name):\n    print('hello ' + name)\n\n\ndef main():\n    greet('world')"
EDIT = ("Edit", {"file_path": "", "old_string": "greet('world')", "new_string": "greet('there')"})

NO_FACE = "x \U0001f600 y \u6f22"
COMMAND = (
    'cd /srv/orbital && python -m tle.verify   "$f"  --strict  || echo "FAILED: $f" >> '
    "/var/log/orbital/a/long/path/to/a/log/file/verify-failures.log\n    # two  spaces kept"
)
UNBROKEN = "A hash with nowhere to break: " + "0123456789abcdef" * 8 + " and the words after it."

LITERAL = '{\n  "tool": "Bash",\n  "exit": 0,\n  "stdout": "a\\tb    c"\n}\n$ ls -la\tdone'

STREAM = (
    "## Streaming\n\nThis text arrives a few characters at a time, with **bold** and a list:\n\n"
    "- first item\n- second item with `code`\n\n```sh\nmake lint typecheck test\n```\n\n"
    "A fenced diff is drawn as one, a line at a time:\n\n"
    "```diff\n--- a/greet.py\n+++ b/greet.py\n@@ -1,3 +1,3 @@\n def main():\n"
    "-    greet('world')\n+    greet('there')\n     return 0\n```\n\n"
    "And a closing paragraph with <b>raw html</b> in it.\n"
)

PARTS_TEXT = (
    ("I will read the file first.\n", "markdown", None),
    ('Read {"file_path": "/src/greet.py"}', "literal", "accent"),
    ("def greet(name):\n    print('hello ' + name)", "literal", "diff_context"),
    ("That confirms it: **one call site**. Editing now.\n", "markdown", None),
    ("Edit failed: anchor not found in file", "wrapped", "danger"),
    ("(reasoning) the anchor has a trailing space\n", "markdown", "text_dim"),
)

STAGES = (("dark", False), ("dark", True), ("light", False), ("high_contrast", False))
FRAMES_PER_STAGE = 30


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT / "shots")
    args = parser.parse_args()

    import screenshot
    from imgui_bundle import hello_imgui, imgui, immapp

    from pptmstr import approval, theme
    from pptmstr.ui import typeset

    edit_file = ROOT / "shots" / ".verify-typeset-edit.py"
    edit_file.parent.mkdir(parents=True, exist_ok=True)
    edit_file.write_text(BEFORE, encoding="utf-8")
    # Read once, here, as the driver does when a call parks: the seam is handed
    # the texts and opens no file.
    edit = approval.diff_texts(EDIT[0], {**EDIT[1], "file_path": str(edit_file)})

    cache = typeset.ViewCache()
    parts = tuple(
        typeset.Part(text, typeset.Kind(kind), colour) for text, kind, colour in PARTS_TEXT
    )
    seen: dict[str, object] = {"frame": 0, "ms": [], "views": {}}
    failures: list[str] = []
    pieces = [STREAM[i : i + 6] for i in range(0, len(STREAM), 6)]

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"{'ok  ' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}")
        if not ok:
            failures.append(name)

    def gui() -> None:
        frame = int(seen["frame"])  # type: ignore[call-overload]
        if frame == 0:
            seen["default_font"] = imgui.get_font().get_debug_name()
        started = time.perf_counter()
        viewport = imgui.get_main_viewport()
        imgui.set_next_window_pos(viewport.work_pos)
        imgui.set_next_window_size(viewport.work_size)
        flags = imgui.WindowFlags_.no_decoration.value | imgui.WindowFlags_.no_move.value
        imgui.begin("verify_typeset", None, flags)
        third = imgui.get_content_region_avail().x / 3.0 - 8.0
        scroll = imgui.WindowFlags_.always_vertical_scrollbar.value
        border = imgui.ChildFlags_.borders.value

        # Sideways too: the fenced block has a line wider than this pane, and code is
        # never broken.
        sideways = imgui.WindowFlags_.horizontal_scrollbar.value
        imgui.begin_child("md", imgui.ImVec2(third, 0.0), border, scroll | sideways)
        typeset.label("markdown()", colour=theme.P.text_dim, weight=typeset.Weight.CODE)
        typeset.markdown(cache, "md", MARKDOWN)
        imgui.end_child()
        imgui.same_line()

        imgui.begin_child("diff", imgui.ImVec2(third, 0.0), border, scroll)
        typeset.label("diff(): an Edit with no closing newline", colour=theme.P.text_dim)
        typeset.diff(cache, "edit", edit)
        typeset.label("diff(): side by side, capped", colour=theme.P.text_dim)
        typeset.diff(cache, "edit-sbs", edit, side_by_side=True, height_cap=120.0)
        typeset.label("plain(): literal", colour=theme.P.text_dim)
        typeset.plain(cache, "lit", LITERAL, colour=theme.P.diff_context)
        typeset.label("plain(wrap=True, mono=False)", colour=theme.P.text_dim)
        typeset.plain(cache, "wrapped", "A wrapped sentence. " * 12, wrap=True, mono=False)
        typeset.label("document(): parts, one view", colour=theme.P.text_dim)
        typeset.document(cache, "parts", parts)
        typeset.label("font(): the faces as native ImGui text", colour=theme.P.text_dim)
        for weight in typeset.Weight:
            typeset.label(f"{weight.value}: Hamburgefonstiv 0123", weight=weight)
        imgui.end_child()
        imgui.same_line()

        imgui.begin_child("stream", imgui.ImVec2(0.0, 0.0), border, scroll)
        typeset.label("plain(wrap=True, exact=True)", colour=theme.P.text_dim)
        typeset.plain(cache, "exact", COMMAND, wrap=True, exact=True)
        typeset.label("markdown(): a word wider than the pane", colour=theme.P.text_dim)
        typeset.markdown(cache, "unbroken", UNBROKEN)
        typeset.label("markdown(): characters no face has", colour=theme.P.text_dim)
        typeset.markdown(cache, "no-face", NO_FACE)
        typeset.label("stream()", colour=theme.P.text_dim, weight=typeset.Weight.CODE)
        handle = typeset.stream(cache, "stream")
        if pieces:
            handle.append(pieces.pop(0))
        elif not handle.closed:
            handle.close()
        handle.draw()
        imgui.end_child()

        imgui.end()
        seen["ms"].append((time.perf_counter() - started) * 1000.0)  # type: ignore[attr-defined]

    def pre_frame() -> None:
        typeset.pre_frame()
        frame = int(seen["frame"])  # type: ignore[call-overload]
        stage, at = divmod(frame, FRAMES_PER_STAGE)
        io = imgui.get_io()
        if stage < len(STAGES):
            if at == 0:
                name, mono = STAGES[stage]
                seen["views"][stage] = cache.view("md")  # type: ignore[index]
                theme.set_theme(name)
                theme.apply_style()
                typeset.configure(typeset=True, mono=mono)
            return
        # The selection probe, after the last stage: a drag in the markdown view,
        # then one in the stream.
        md, live = cache.view("md"), cache.view("stream")
        if at in (0, 6) and md is not None and live is not None:
            view = md if at == 0 else live
            origin = view.origin
            if origin is not None:
                seen["drag"] = (origin[0] + 4.0, origin[1] + 12.0)
                io.add_mouse_pos_event(*seen["drag"])  # type: ignore[misc]
        elif at in (1, 7):
            io.add_mouse_button_event(0, True)
        elif at in (2, 8):
            x, y = seen["drag"]  # type: ignore[misc]
            io.add_mouse_pos_event(x + 160.0, y)
        elif at in (3, 9):
            io.add_mouse_button_event(0, False)
        elif at == 5 and md is not None:
            seen["md_selected"] = md.selected_text()
            seen["md_owned"] = md.owns_selection
        elif at == 11 and md is not None and live is not None:
            seen["live_selected"] = live.selected_text()
            seen["md_owned_after"] = md.owns_selection
            seen["live_owned"] = live.owns_selection

    def before_swap() -> None:
        frame = int(seen["frame"])  # type: ignore[call-overload]
        stage, at = divmod(frame, FRAMES_PER_STAGE)
        if at == FRAMES_PER_STAGE - 1 and stage < len(STAGES):
            name, mono = STAGES[stage]
            screenshot._grab(args.out / f"typeset-seam-{name}-{'mono' if mono else 'prose'}.png")
            seen[f"stats-{stage}"] = {
                key: (cache.drawn(key), cache.replaced(key)) for key in cache.keys()
            }
        if stage == len(STAGES) and at == 12:
            screenshot._grab(args.out / "typeset-seam-selection.png")

    def after_swap() -> None:
        seen["frame"] = int(seen["frame"]) + 1  # type: ignore[call-overload]
        if int(seen["frame"]) >= FRAMES_PER_STAGE * len(STAGES) + 14:  # type: ignore[call-overload]
            hello_imgui.get_runner_params().app_shall_exit = True

    params = hello_imgui.RunnerParams()
    params.app_window_params.window_title = "verify_typeset"
    params.app_window_params.window_geometry.size = (1500, 900)
    params.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.no_default_window
    )
    params.ini_disable = True
    params.fps_idling.enable_idling = False
    loading = {"s": 0.0}

    def load_fonts() -> None:
        started = time.perf_counter()
        theme.load_fonts()
        loading["s"] = time.perf_counter() - started

    params.callbacks.load_additional_fonts = load_fonts
    params.callbacks.pre_new_frame = pre_frame
    params.callbacks.show_gui = gui
    params.callbacks.before_swap = before_swap
    params.callbacks.after_swap = after_swap
    theme.set_theme("dark")
    typeset.configure(typeset=True, mono=False)
    immapp.run(params)
    edit_file.unlink(missing_ok=True)

    fonts = typeset._STATE.fonts
    print(f"theme.load_fonts took {loading['s']:.2f}s")
    check("Inconsolata is still the default font", "Inconsolata" in str(seen.get("default_font")))
    check(
        "every face loaded, none failed, none pending",
        fonts is not None and fonts.failed == () and fonts.pending == (),
        "" if fonts is None else f"failed={fonts.failed} pending={fonts.pending}",
    )
    for stage, (name, mono) in enumerate(STAGES):
        stats = dict(seen.get(f"stats-{stage}") or {})  # type: ignore[call-overload]
        no_face = stats.pop("no-face", None)
        drawn = {k: (*v[0], v[1]) for k, v in stats.items() if v[0] is not None}
        label = f"{name}/{'mono' if mono else 'prose'}"
        check(
            f"{label}: every view drew glyphs, none missing drawn, none replaced built",
            len(drawn) == 9 and all(g > 0 and m == 0 and r == 0 for g, m, r in drawn.values()),
            str(drawn),
        )
        # (glyphs, missing) says nothing is wrong with this one. `replaced` does.
        check(
            f"{label}: characters no face has are four glyphs, none missing, two replaced",
            no_face == ((4, 0), 2),
            str(no_face),
        )
    # views[n] is the markdown view as stage n began, so as stage n-1 left it.
    views = seen["views"]
    check(
        "switching to monospace prose rebuilt the markdown view",
        views[2] is not views[1],  # type: ignore[index]
    )
    check(
        "a palette switch alone (light to high contrast) did not",
        views[3] is not None and cache.view("md") is views[3],  # type: ignore[index]
    )
    check(
        "a drag selected text in the markdown view",
        bool(seen.get("md_selected")) and seen.get("md_owned") is True,
        repr(seen.get("md_selected")),
    )
    check(
        "a second drag, in the stream, took the selection from it",
        bool(seen.get("live_selected"))
        and seen.get("live_owned") is True
        and seen.get("md_owned_after") is False,
        repr(seen.get("live_selected")),
    )
    ms = sorted(seen["ms"][5:])  # type: ignore[index]
    print(
        f"gui callback over {len(ms)} frames: median {ms[len(ms) // 2]:.2f} ms, "
        f"p95 {ms[int(len(ms) * 0.95)]:.2f} ms, max {ms[-1]:.2f} ms (llvmpipe if under Xvfb)"
    )
    print("PASS" if not failures else f"FAIL: {failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
