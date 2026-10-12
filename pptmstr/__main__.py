"""
The entry point: ``pptmstr`` and ``python -m pptmstr``.

Two things happen here before the application is imported, and each is here
because it has to come first:

- **The text library's two variables are settled** (``pairing.adopt``), before
  anything asks advtext whether there is a library.
- **``--version`` is answered** without the application: importing it is every
  pane and a second and a half, and the report is for installs where something
  may be wrong with exactly what that import needs.
"""

from __future__ import annotations

import sys

from . import pairing, version


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    pairing.adopt()
    if "--version" in args:
        return version.main()
    from .app import main as run

    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
