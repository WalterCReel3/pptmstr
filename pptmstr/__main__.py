"""
The entry point: ``pptmstr`` and ``python -m pptmstr``.

Three things happen here before the application is imported, and each is here
because it has to come first:

- **The text library's two variables are settled** (``pairing.adopt``), before
  anything asks imscribe whether there is a library.
- **``--version`` is answered** without the application: importing it is every
  pane and a second and a half, and the report is for installs where something
  may be wrong with exactly what that import needs.
- **The library is asked for, and what ``adopt`` set is taken back out of the
  environment** (``gate.settle``), before the application starts a thread or a
  session: a session's process inherits this one's environment as it is when
  the session is launched, and ``--task`` launches before the window opens.

**Which ``--version`` is the flag is the application's parser's to say.** It
knows which options take a value; this module does not import it to ask. So
``--version`` is answered here only where it cannot be anything else -- nothing
before it but values and ``name=value`` options, and no ``--`` -- and any other
command line goes to the parser, which answers the flag the same way
(``app.main``) and refuses ``--task --version`` as it refuses any option with
no value. What that costs is the application's import for ``--fake --version``.
"""

from __future__ import annotations

import sys

from . import pairing, version


def _asks_for_version(args: list[str]) -> bool:
    """
    Whether ``--version`` is on the command line where only the flag can be.
    """
    for at, arg in enumerate(args):
        if arg == "--":
            return False
        if arg == "--version":
            before = args[at - 1] if at else ""
            return not before.startswith("-") or "=" in before
    return False


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    pairing.adopt()
    if _asks_for_version(args):
        return version.main()
    from .ui.typeset import gate

    gate.settle()
    from .app import main as run

    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
