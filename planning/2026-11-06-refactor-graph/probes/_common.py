"""
Shared setup for the probes: isolate HOME/XDG_CONFIG_HOME, pin the import path to
this worktree, and AST helpers.

Imported before any pptmstr module. ``settings.config_dir`` reads XDG_CONFIG_HOME
(honoured only when absolute) and otherwise ``Path.home() / ".config"``, both at
call time, so setting the two variables here, before the first import, puts every
config read or write under a fresh temporary directory.
"""

from __future__ import annotations

import ast
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
_base = os.environ.get("TMPDIR") or tempfile.gettempdir()
SANDBOX = Path(tempfile.mkdtemp(prefix="pptmstr-probe-", dir=_base))
os.environ["HOME"] = str(SANDBOX / "home")
os.environ["XDG_CONFIG_HOME"] = str(SANDBOX / "xdg")
(SANDBOX / "home").mkdir()
(SANDBOX / "xdg").mkdir()

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pptmstr  # noqa: E402
from pptmstr import settings  # noqa: E402

assert Path(pptmstr.__file__).resolve().is_relative_to(ROOT), pptmstr.__file__
_cfg = settings.config_dir()
assert _cfg.is_relative_to(SANDBOX), _cfg
assert not str(_cfg).startswith(str(Path("/home/wreel/.config"))), _cfg


def source_of(relpath: str) -> str:
    return (ROOT / relpath).read_text(encoding="utf-8")


def find_def(tree: ast.AST, name: str, cls: str | None = None) -> ast.FunctionDef:
    """The (unique) function ``name``, at module level or inside class ``cls``."""
    scope: list[ast.stmt]
    if cls is None:
        scope = tree.body  # type: ignore[attr-defined]
    else:
        (klass,) = [
            n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls  # type: ignore[attr-defined]
        ]
        scope = klass.body
    found = [n for n in scope if isinstance(n, ast.FunctionDef) and n.name == name]
    if len(found) != 1:
        raise LookupError(f"{name} in {cls or 'module'}: {len(found)} definitions")
    return found[0]


def body_without_docstring(fn: ast.FunctionDef) -> list[ast.stmt]:
    body = list(fn.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return body


def dump(nodes: list[ast.stmt] | ast.AST) -> str:
    if isinstance(nodes, list):
        return "\n".join(ast.dump(n, include_attributes=False) for n in nodes)
    return ast.dump(nodes, include_attributes=False)


def verdict(word: str, why: str) -> None:
    print(f"VERDICT: {word} -- {why}")
