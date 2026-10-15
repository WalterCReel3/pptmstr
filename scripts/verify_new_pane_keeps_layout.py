"""
Does adding a pane cost an operator their saved layout, and does ``layout_ini``
stop it?

Three runs of hello_imgui over copies of one saved ini, with the window lists
``app._triage_layout`` and ``app._focus_layout`` declare and panes that draw
nothing. Each reads, from Dear ImGui's own window records, which dock node every
pane is in -- in the layout the ini opens on, and again after switching to the
other one.

  control   the declared windows without the new pane, the ini as it is.
            Where the operator's panes are when nothing has changed.
  added     the new pane declared, the ini as it is.
            What a first launch does with no help.
  kept      the new pane declared, the ini passed through
            ``layout_ini.keep_layout_for`` first, as ``app.main`` does.

The verdict is computed from those node ids and nothing else: ``kept`` holds when
every pane ``control`` saw is in the node ``control`` saw it in, in both layouts,
and the new pane is in its neighbour's node. ``added`` is reported, not required:
it is the measurement the module's docstring cites, and an ini whose panes were
never moved off the coded arrangement shows no difference there.

**What this does not establish.** The null platform and renderer backends are
used, so no window opened and nothing was drawn: this says which node a pane is
docked in, not what the tab bar looks like or which tab is in front. The display
size is the null backend's, not the size the ini was saved at.

    .venv/bin/python scripts/verify_new_pane_keeps_layout.py [--ini PATH]

``--ini`` defaults to the operator's own: ``pptmstr.ini`` under ``XDG_CONFIG_HOME``
when that is absolute and under ``~/.config`` when it is not, which is where
hello_imgui keeps it. Measuring that file is what this is for, so it is not
redirected; it is only ever read, every run is over a copy in a fresh temporary
directory, and the first line of output names the file before it is opened.
``app.main`` is never run: the runner here is built by hand with an absolute ini
path, so no settings are read and nothing under the configuration directory or
``~/.claude`` is touched.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NEW = "DIGEST"
BESIDE = "BOARD"
_SAMPLE_FIRST = 10
_SWITCH = 30
_SAMPLE_SECOND = 40
_EXIT = 45


def _child(ini: Path, declare_new: bool) -> int:
    """One run. Prints ``{layout: {label: dock node id}}`` as a line of JSON."""
    from imgui_bundle import hello_imgui, imgui

    from pptmstr import app

    labels = ("FLEET", "NEEDS YOU", "CONTEXT", "DETAIL", "BOARD", NEW, "BRIEF", "SESSION", "HEALTH")
    panels = {label: (lambda: None) for label in (*labels, "LOG")}
    layouts = [app._triage_layout(panels), app._focus_layout(panels)]
    declared = {w.label for layout in layouts for w in layout.dockable_windows}
    if NEW not in declared:
        print(f"neither layout declares {NEW}", file=sys.stderr)
        return 2
    if not declare_new:
        for layout in layouts:
            layout.dockable_windows = [w for w in layout.dockable_windows if w.label != NEW]

    runner = hello_imgui.RunnerParams()
    runner.app_window_params.window_title = "pptmstr"
    runner.imgui_window_params.default_imgui_window_type = (
        hello_imgui.DefaultImGuiWindowType.provide_full_screen_dock_space
    )
    runner.docking_params = layouts[0]
    runner.alternative_docking_layouts = layouts[1:]
    runner.remember_selected_alternative_layout = True
    runner.ini_folder_type = hello_imgui.IniFolderType.absolute_path
    runner.ini_filename = str(ini)
    runner.platform_backend_type = hello_imgui.PlatformBackendType.null
    runner.renderer_backend_type = hello_imgui.RendererBackendType.null
    runner.fps_idling.enable_idling = False

    def fonts() -> None:
        # The null renderer builds no font atlas, and Dear ImGui asserts on a
        # frame without one unless the backend says it takes textures on demand.
        imgui.get_io().backend_flags |= imgui.BackendFlags_.renderer_has_textures

    seen: dict[str, dict[str, int]] = {}
    frame = [0]

    def sample() -> None:
        docks: dict[str, int] = {}
        for label in labels:
            window = imgui.internal.find_window_by_name(label)
            if window is not None and window.dock_node is not None:
                docks[label] = int(window.dock_id)
            elif window is not None:
                docks[label] = 0
        seen[hello_imgui.current_layout_name()] = docks

    def pre_frame() -> None:
        frame[0] += 1
        if frame[0] in (_SAMPLE_FIRST, _SAMPLE_SECOND):
            sample()
        if frame[0] == _SWITCH:
            other = app.FOCUS if hello_imgui.current_layout_name() == app.TRIAGE else app.TRIAGE
            hello_imgui.switch_layout(other)
        if frame[0] == _EXIT:
            runner.app_shall_exit = True

    runner.callbacks.load_additional_fonts = fonts
    runner.callbacks.pre_new_frame = pre_frame
    hello_imgui.run(runner)
    print(json.dumps(seen))
    return 0


def _run(saved: Path, base: Path, name: str, *, declare_new: bool, patch: bool) -> dict:
    from pptmstr import layout_ini

    work = base / name
    work.mkdir()
    ini = work / "pptmstr.ini"
    shutil.copyfile(saved, ini)
    if patch:
        print(f"  {name}: {layout_ini.keep_layout_for(ini, NEW, BESIDE)}")
    done = subprocess.run(
        [sys.executable, __file__, "--child", str(ini), "--declare", "1" if declare_new else "0"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=ROOT,
    )
    if done.returncode != 0:
        raise SystemExit(f"{name} run failed ({done.returncode}):\n{done.stderr[-2000:]}")
    return json.loads(done.stdout.strip().splitlines()[-1])


def _show(name: str, seen: dict) -> None:
    for layout, docks in seen.items():
        cells = "  ".join(f"{label}={node:#x}" for label, node in sorted(docks.items()))
        print(f"  {name:8} {layout:7} {cells}")


def _saved_ini() -> Path:
    """
    Where the application's ini is: hello_imgui's ``app_user_config_folder``, which
    on Linux is ``XDG_CONFIG_HOME`` when absolute and ``~/.config`` otherwise.
    """
    raw = os.environ.get("XDG_CONFIG_HOME", "")
    return (Path(raw) if raw.startswith("/") else Path.home() / ".config") / "pptmstr.ini"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--ini", type=Path, default=None)
    ap.add_argument("--child", type=Path, default=None, help=argparse.SUPPRESS)
    ap.add_argument("--declare", default="1", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.child is not None:
        return _child(args.child, args.declare == "1")
    if args.ini is None:
        args.ini = _saved_ini()
        print(f"reading the operator's own saved layout, {args.ini}: copied, never written")
    else:
        print(f"reading the saved layout at {args.ini}: copied, never written")
    if not args.ini.is_file():
        print(f"no saved layout at {args.ini}: nothing to measure")
        return 2

    import imgui_bundle

    print(f"imgui_bundle {imgui_bundle.__version__}, null backends, ini copied from {args.ini}")
    with tempfile.TemporaryDirectory(prefix="verify-new-pane-") as tmp:
        base = Path(tmp)
        control = _run(args.ini, base, "control", declare_new=False, patch=False)
        added = _run(args.ini, base, "added", declare_new=True, patch=False)
        kept = _run(args.ini, base, "kept", declare_new=True, patch=True)
    for name, seen in (("control", control), ("added", added), ("kept", kept)):
        _show(name, seen)

    def moved(seen: dict) -> list[str]:
        return [
            f"{layout}/{label}: {node:#x} -> {seen.get(layout, {}).get(label, -1):#x}"
            for layout, docks in control.items()
            for label, node in docks.items()
            if seen.get(layout, {}).get(label) != node
        ]

    lost = moved(added)
    print(
        f"added, no help: {len(lost)} pane(s) left the node they were saved in"
        + (f" ({'; '.join(lost)})" if lost else "")
    )
    still_moved = moved(kept)
    apart = [
        layout
        for layout, docks in kept.items()
        if not docks.get(NEW) or docks.get(NEW) != docks.get(BESIDE)
    ]
    both = set(kept) == {"TRIAGE", "FOCUS"} and set(control) == {"TRIAGE", "FOCUS"}
    ok = both and not still_moved and not apart
    print(
        f"kept: {'PASS' if ok else 'FAIL'} -- "
        f"{len(still_moved)} pane(s) moved from where control saw them"
        f"{' (' + '; '.join(still_moved) + ')' if still_moved else ''}; "
        f"{NEW} is in {BESIDE}'s node in {len(kept) - len(apart)} of {len(kept)} layout(s)"
        f"{'' if both else '; not both layouts were sampled'}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
