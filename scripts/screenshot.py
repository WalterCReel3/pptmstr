#!/usr/bin/env python3
"""
Run the app for N frames, grab the framebuffer, write a PNG, exit.

Exists so the UI can be looked at without a human in the loop -- which for an
immediate-mode UI is the only way to check that a change did what it claimed.
It runs the same code paths as a normal launch; it only adds a frame-count exit
and a glReadPixels.

PNG is written by hand via zlib rather than pulling in Pillow. The encoder is
twenty lines and this is a dev script -- a dependency here would be paid for by
everyone who installs the project.
"""

from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def write_png(path: Path, width: int, height: int, rgb_rows: list[bytes]) -> None:
    """Minimal truecolour 8-bit PNG, filter type 0 on every row."""
    raw = b"".join(b"\x00" + row for row in rgb_rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "shot.png")
    ap.add_argument("--frames", type=int, default=120)
    ap.add_argument("--focus", default=None, help="dockable window to bring to front")
    ap.add_argument("--argv", nargs=argparse.REMAINDER, default=[])
    args = ap.parse_args()

    from imgui_bundle import hello_imgui, immapp

    from pptmstr import app as pptmstr_app

    real_run = immapp.run
    counter = {"n": 0}

    def patched_run(runner: object, *rest: object) -> object:
        user_after = runner.callbacks.after_swap  # type: ignore[attr-defined]

        def after_swap() -> None:
            if user_after:
                user_after()
            counter["n"] += 1
            if args.focus and counter["n"] == 2:
                hello_imgui.get_runner_params().docking_params.focus_dockable_window(args.focus)
            if counter["n"] == args.frames:
                _grab(args.out)
                hello_imgui.get_runner_params().app_shall_exit = True

        runner.callbacks.after_swap = after_swap  # type: ignore[attr-defined]
        # Idling would make the frame count take wall-clock time proportional to the
        # idle rate, and a screenshot run has no operator to wake it.
        runner.fps_idling.enable_idling = False  # type: ignore[attr-defined]
        return real_run(runner, *rest)

    immapp.run = patched_run  # type: ignore[assignment]
    try:
        return int(pptmstr_app.main(args.argv or ["--fake"]))
    finally:
        immapp.run = real_run  # type: ignore[assignment]


def _grab(out: Path) -> None:
    from imgui_bundle import imgui
    from OpenGL import GL

    io = imgui.get_io()
    width = int(io.display_size.x * io.display_framebuffer_scale.x)
    height = int(io.display_size.y * io.display_framebuffer_scale.y)

    GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
    GL.glReadBuffer(GL.GL_FRONT)
    data = GL.glReadPixels(0, 0, width, height, GL.GL_RGB, GL.GL_UNSIGNED_BYTE)
    if not isinstance(data, bytes):
        data = bytes(data)

    stride = width * 3
    # OpenGL's origin is bottom-left and PNG's is top-left, so the rows come out
    # upside down unless reversed here.
    rows = [data[y * stride : (y + 1) * stride] for y in range(height - 1, -1, -1)]
    write_png(out, width, height, rows)
    print(f"wrote {out} ({width}x{height})")


if __name__ == "__main__":
    raise SystemExit(main())
