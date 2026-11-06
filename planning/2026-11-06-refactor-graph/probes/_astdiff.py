"""
Alpha-renaming and a parallel AST diff that names the exact differing nodes.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass


class Canon(ast.NodeTransformer):
    """
    Rename every ``Name`` and ``arg`` to ``v<n>`` in order of first appearance.
    With ``attrs=True``, ``Attribute.attr`` too (to ``a<n>``). Constants are left.
    """

    def __init__(self, attrs: bool = False) -> None:
        self.names: dict[str, str] = {}
        self.attrs_map: dict[str, str] = {}
        self.attrs = attrs

    def _n(self, name: str) -> str:
        return self.names.setdefault(name, f"v{len(self.names)}")

    def visit_Name(self, node: ast.Name) -> ast.AST:
        node.id = self._n(node.id)
        return node

    def visit_arg(self, node: ast.arg) -> ast.AST:
        node.arg = self._n(node.arg)
        node.annotation = None
        return node

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        self.generic_visit(node)
        if self.attrs:
            node.attr = self.attrs_map.setdefault(node.attr, f"a{len(self.attrs_map)}")
        return node


def canon(nodes: list[ast.stmt], attrs: bool = False) -> tuple[list[ast.stmt], Canon]:
    c = Canon(attrs)
    out = [c.visit(copy.deepcopy(n)) for n in nodes]
    return out, c


@dataclass
class Diff:
    path: str
    line_a: int | None
    line_b: int | None
    a: str
    b: str


def _show(x: object) -> str:
    if isinstance(x, ast.AST):
        try:
            return ast.unparse(x)
        except Exception:
            return ast.dump(x)
    if isinstance(x, list):
        return "[" + ", ".join(_show(i) for i in x) + "]"
    return repr(x)


def diff(a: object, b: object, path: str = "", out: list[Diff] | None = None) -> list[Diff]:
    """Every minimal pair of differing subtrees."""
    if out is None:
        out = []
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(Diff(path, _line(a), _line(b), _show(a), _show(b)))
            return out
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f"{path}[{i}]", out)
        return out
    if isinstance(a, ast.AST) and isinstance(b, ast.AST):
        if type(a) is not type(b):
            out.append(Diff(path, _line(a), _line(b), _show(a), _show(b)))
            return out
        # Leaf-field differences (names, constants, operators' types) are reported
        # at the node that holds them, so the reader sees the whole expression.
        leaf_differs = [
            f
            for f in a._fields
            if not isinstance(getattr(a, f), (ast.AST, list))
            and getattr(a, f) != getattr(b, f)
        ]
        if leaf_differs:
            out.append(Diff(f"{path}.{type(a).__name__}({','.join(leaf_differs)})", _line(a), _line(b), _show(a), _show(b)))
            return out
        for f in a._fields:
            diff(getattr(a, f), getattr(b, f), f"{path}.{f}", out)
        return out
    if a != b:
        out.append(Diff(path, None, None, _show(a), _show(b)))
    return out


def _line(x: object) -> int | None:
    if isinstance(x, list):
        return _line(x[0]) if x else None
    return getattr(x, "lineno", None)


def report(diffs: list[Diff], src_a: list[str], src_b: list[str], label_a: str, label_b: str) -> None:
    for d in diffs:
        print(f"  at {d.path}")
        if d.line_a:
            print(f"    {label_a}:{d.line_a}: {src_a[d.line_a - 1].strip()}")
        if d.line_b:
            print(f"    {label_b}:{d.line_b}: {src_b[d.line_b - 1].strip()}")
        print(f"    canon a: {d.a}")
        print(f"    canon b: {d.b}")
