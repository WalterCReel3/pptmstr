"""
The layer rules the tree states in prose, checked against its imports.

Each rule below was a sentence in a docstring, in ``STYLE.md`` or in a planning
record, and nothing held the code to it (``planning/2026-11-06-modules-are-named-
for-what-they-require.md``, T3, T7 and A7). Thirteen are checked here:

1. A module whose docstring says it imports no ImGui imports no ``imgui_bundle``,
   directly in any form, and does not reach it through the imports that run when
   it is imported.
2. ``store`` imports no module that reads the disk or asks it where a path leads,
   directly or through the imports that run when it is imported, from whichever
   of its modules.
3. No module outside ``pptmstr/ui`` imports ``pptmstr.ui``, the two composition
   roots aside.
4. A module the docstrings or ``STYLE.md`` call pure calls no clock.
5. ``bus`` and ``commandbus`` import nothing of each other (A6).
6. ``naming`` imports nothing from pptmstr, so it reaches neither ``board`` nor
   ``ui``, and no name ``naming`` or ``addresses`` holds is imported through
   ``board`` (A1).
7. ``serialworker`` imports nothing from pptmstr (B3).
8. ``ids`` imports nothing from pptmstr and calls no clock, and it is the one
   module that imports ``uuid`` or ``secrets`` (B6).
9. ``pptmstr.channels`` imports nothing, ``channels.records`` imports nothing from
   pptmstr, only ``app`` imports ``channels.log``, and a name ``records`` defines
   is imported from ``records`` alone (B6). ``channels.facts`` reaches no disk
   reader, and nothing importing it runs starts a thread.
10. ``pptmstr.board`` imports nothing, ``board.words`` imports nothing from
    pptmstr when it runs, and a name ``words`` defines is imported from ``words``
    alone (B5).
11. Only ``pptmstr.store`` imports a module of ``pptmstr.store``: ``_apply`` is the
    one door into the step its modules are parts of (B9).
12. ``gate.policy``, ``gate.tools`` and ``gate.diff`` import nothing from
    pptmstr, and a name one of them defines is imported from it alone (B5).
13. Among the modules outside ``pptmstr/ui``, the one import cycle is ``command``
    and ``model``, and ``command`` imports ``model`` under ``TYPE_CHECKING`` only.

**Read from the source, never imported.** Every import is collected with ``ast``,
including those inside a function (``lazy``) and those under ``if TYPE_CHECKING:``
(``typing``), and each is tagged with its kind, so a rule can say which kinds it
covers. Importing the modules would see only the eager edges, and would need a
working ImGui to see those.

**Every exception is exact, and a stale one fails.** The tree breaks two of these
rules today, and each break is named below with its reason. An exception that no
longer matches -- the edge gone, or changed kind -- fails with a request to delete
it, so the lists shrink as the breaks are fixed (A6) rather than outliving them.
The same holds for each cited claim: a docstring that stops saying it imports no
ImGui fails here until its entry goes.

What this cannot see: an import made by ``importlib`` or ``__import__``, a clock
handed in as a reference and called through another name, and a disk read through
any call not in ``_DISK_READS`` -- or, for the store, not in ``_DISK_TOUCHES``.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "pptmstr"

Kind = Literal["eager", "lazy", "typing"]


@dataclass(frozen=True)
class Import:
    """
    One imported name's module, as seen from ``source``.
    """

    source: str
    target: str
    kind: Kind
    line: int


# -- the scan -------------------------------------------------------------------


@cache
def modules() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = path.relative_to(ROOT).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        found[".".join(parts)] = path
    return found


@cache
def tree_of(module: str) -> ast.Module:
    return ast.parse(modules()[module].read_text(encoding="utf-8"))


def _targets(module: str, is_package: bool, node: ast.Import | ast.ImportFrom) -> list[str]:
    """
    The modules ``node`` binds names from: ``from pkg import sub`` names ``pkg.sub``
    when that is a module of the tree, and ``pkg`` when ``sub`` is an attribute.
    """
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    full = _from(module, is_package, node)
    known = modules()
    return [
        f"{full}.{alias.name}" if f"{full}.{alias.name}" in known else full for alias in node.names
    ]


def _from(module: str, is_package: bool, node: ast.ImportFrom) -> str:
    """
    The absolute name of what ``node`` imports from: ``pptmstr.ondisk`` for ``from
    .ondisk import x`` in ``pptmstr.brief``, ``pptmstr`` for ``from . import x``.
    """
    if not node.level:
        return node.module or ""
    package = module.split(".") if is_package else module.split(".")[:-1]
    base = ".".join(package[: len(package) - (node.level - 1)])
    return f"{base}.{node.module}" if node.module else base


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def scan(module: str, tree: ast.Module, is_package: bool) -> list[Import]:
    """
    Every import in ``tree``, tagged: ``typing`` under ``if TYPE_CHECKING:`` wherever
    that is, else ``lazy`` inside a function, else ``eager``.
    """
    found: list[Import] = []

    def visit(node: ast.AST, kind: Kind) -> None:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for target in _targets(module, is_package, node):
                found.append(Import(module, target, kind, node.lineno))
            return
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            for child in node.body:
                visit(child, "typing")
            for child in node.orelse:
                visit(child, kind)
            return
        inner: Kind = kind
        if kind == "eager" and isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
        ):
            inner = "lazy"
        for nested in ast.iter_child_nodes(node):
            visit(nested, inner)

    visit(tree, "eager")
    return found


@cache
def imports() -> tuple[Import, ...]:
    out: list[Import] = []
    for module, path in modules().items():
        out.extend(scan(module, tree_of(module), path.name == "__init__.py"))
    return tuple(out)


def imports_of(module: str) -> list[Import]:
    return [edge for edge in imports() if edge.source == module]


def is_imgui(target: str) -> bool:
    return target.split(".")[0] in ("imgui_bundle", "imgui")


def is_ui(module: str) -> bool:
    return module == "pptmstr.ui" or module.startswith("pptmstr.ui.")


def docstring_of(path: Path) -> str:
    return ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""


def _dotted(func: ast.expr, aliases: dict[str, str]) -> str | None:
    """
    ``time.monotonic`` for ``time.monotonic()``, ``monotonic()`` after ``from time
    import monotonic``, and ``dt.now()`` after ``from datetime import datetime as dt``.
    """
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if not isinstance(func, ast.Name):
        return None
    parts.append(func.id)
    parts.reverse()
    return ".".join([aliases.get(parts[0], parts[0]), *parts[1:]])


def _aliases(tree: ast.Module, module: str | None, is_package: bool) -> dict[str, str]:
    """
    What each imported name stands for. A relative import resolves only when the
    module it is in is given; without one it binds nothing.
    """
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and (module is not None or not node.level):
            source = _from(module or "", is_package, node)
            for alias in node.names:
                bound[alias.asname or alias.name] = f"{source}.{alias.name}"
    return bound


@dataclass(frozen=True)
class Call:
    dotted: str
    function: str
    line: int
    positional: int = 0


def calls(tree: ast.Module, module: str | None = None, is_package: bool = False) -> Iterator[Call]:
    """
    Every call whose callee is a dotted name, with the innermost enclosing function
    (``<module>`` at the top level). Given the ``module`` the tree is, a name bound
    by a relative import is called by its absolute name.
    """
    aliases = _aliases(tree, module, is_package)

    def visit(node: ast.AST, function: str) -> Iterator[Call]:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if isinstance(node, ast.Call):
            dotted = _dotted(node.func, aliases)
            if dotted is not None:
                yield Call(dotted, function, node.lineno, len(node.args))
            elif isinstance(node.func, ast.Attribute):
                yield Call(f"?.{node.func.attr}", function, node.lineno, len(node.args))
        for child in ast.iter_child_nodes(node):
            yield from visit(child, function)

    yield from visit(tree, "<module>")


def calls_of(module: str) -> Iterator[Call]:
    """
    ``calls`` over a module of the tree, with its relative imports resolved.
    """
    return calls(tree_of(module), module, modules()[module].name == "__init__.py")


# -- the scan, checked ----------------------------------------------------------


def test_the_scan_tags_each_kind_of_import() -> None:
    source = (
        "from __future__ import annotations\n"
        "import os\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from .model import Snapshot\n"
        "def f():\n"
        "    from .ui import widgets\n"
        "    if TYPE_CHECKING:\n"
        "        import imgui_bundle\n"
        "class C:\n"
        "    import json\n"
        "    def g(self):\n"
        "        from ..theme import Color\n"
    )
    found = {
        (edge.target, edge.kind)
        for edge in scan("pptmstr.ui.example", ast.parse(source), is_package=False)
    }
    assert found == {
        ("__future__", "eager"),
        ("os", "eager"),
        ("typing", "eager"),
        ("pptmstr.ui.model", "typing"),
        ("pptmstr.ui.ui", "lazy"),
        ("imgui_bundle", "typing"),
        ("json", "eager"),
        ("pptmstr.theme", "lazy"),
    }


def test_the_scan_finds_all_three_kinds_in_the_tree() -> None:
    """
    A scan that found nothing would pass every rule below.
    """
    kinds = {edge.kind for edge in imports()}
    assert kinds == {"eager", "lazy", "typing"}
    assert any(
        edge.source == "pptmstr.store" and edge.target == "pptmstr.model" and edge.kind == "eager"
        for edge in imports()
    )
    assert any(
        edge.source == "pptmstr.ui.typeset.views"
        and edge.target == "pptmstr.ui.typeset.figure_host"
        and edge.kind == "lazy"
        for edge in imports()
    )


def test_the_clock_scan_resolves_an_imported_name() -> None:
    source = (
        "import time\n"
        "from time import monotonic as tick\n"
        "from datetime import datetime\n"
        "def a(): return time.time()\n"
        "def b(): return tick()\n"
        "def c(): return datetime.now()\n"
    )
    found = {(call.dotted, call.function) for call in calls(ast.parse(source))}
    assert {
        ("time.time", "a"),
        ("time.monotonic", "b"),
        ("datetime.datetime.now", "c"),
    } <= found


# -- 1. what claims no ImGui imports none -----------------------------------------


@dataclass(frozen=True)
class Claim:
    """
    Where a rule is stated: a file under the repository, and words it holds.
    For a module, the words are looked for in its module docstring.
    """

    file: str
    words: str

    def holds(self) -> bool:
        path = ROOT / self.file
        text = docstring_of(path) if path.suffix == ".py" else path.read_text(encoding="utf-8")
        return self.words in " ".join(text.split())


# Every module whose docstring says it imports no ImGui, with the words that say so.
NO_IMGUI: dict[str, Claim] = {
    # addresses.py:16
    "pptmstr.addresses": Claim("pptmstr/addresses.py", "Imports no imgui"),
    # board/rows.py:24
    "pptmstr.board.rows": Claim("pptmstr/board/rows.py", "Imports no imgui"),
    # board/digest.py:67
    "pptmstr.board.digest": Claim("pptmstr/board/digest.py", "Imports no imgui and no SDK"),
    # commandtext.py:4
    "pptmstr.commandtext": Claim("pptmstr/commandtext.py", "Pure functions: no UI, no IO"),
    # shellscan.py:4
    "pptmstr.shellscan": Claim("pptmstr/shellscan.py", "Pure functions: no SDK, no UI, no IO"),
    # gate/rows.py:4
    "pptmstr.gate.rows": Claim("pptmstr/gate/rows.py", "Pure: no SDK, no UI, no clock, no IO"),
    # gate/reach.py:11
    "pptmstr.gate.reach": Claim("pptmstr/gate/reach.py", "No SDK and no UI"),
    # gate/classify.py:9
    "pptmstr.gate.classify": Claim("pptmstr/gate/classify.py", "No SDK and no UI"),
    # gate/park.py:8
    "pptmstr.gate.park": Claim("pptmstr/gate/park.py", "No SDK and no UI"),
    # figures/__init__.py:4-5
    "pptmstr.figures.displaylist": Claim(
        "pptmstr/figures/__init__.py",
        "Pure, and testable without ImGui or a child process: ``sanitise``, the policy"
        " every SVG passes before it is drawn, and ``displaylist``",
    ),
    # figures/sanitise.py:5-6
    "pptmstr.figures.sanitise": Claim(
        "pptmstr/figures/sanitise.py", "stdlib only, no I/O, no clock, no ImGui"
    ),
    # ui/blocks.py:4
    "pptmstr.ui.blocks": Claim("pptmstr/ui/blocks.py", "No ImGui here"),
    # ui/fleet_view.py:59
    "pptmstr.ui.fleet_view": Claim("pptmstr/ui/fleet_view.py", "Imports no imgui"),
    # ui/gate_menu.py:41
    "pptmstr.ui.gate_menu": Claim("pptmstr/ui/gate_menu.py", "Imports no imgui"),
    # ui/projects.py:10
    "pptmstr.ui.projects": Claim("pptmstr/ui/projects.py", "Imports no imgui"),
    # ui/span_layout.py:5
    "pptmstr.ui.span_layout": Claim("pptmstr/ui/span_layout.py", "No ImGui here"),
    # ui/splash.py:11
    "pptmstr.ui.splash": Claim("pptmstr/ui/splash.py", "No ImGui here, and no clock read"),
    # ui/typeset/parts.py:4
    "pptmstr.ui.typeset.parts": Claim(
        "pptmstr/ui/typeset/parts.py", "Pure: no library, no ImGui, no state"
    ),
}

# Module docstrings that say "no ImGui" of something narrower than the module.
NOT_A_MODULE_CLAIM: dict[str, str] = {
    "pptmstr.figures": "states it for ``sanitise`` and ``displaylist``, which NO_IMGUI holds",
    "pptmstr.ui.transcript_pane": "says it of ``TypesetFeed``, one class of a pane that draws",
    "pptmstr.version": "says it makes no ImGui context; it imports the library to report it",
}

# A claimed module that reaches ImGui through its eager imports, and the first module
# on that path that imports it. Each is a break of its claim, kept until it is fixed.
NO_IMGUI_REACHED_THROUGH: dict[str, str] = {
    # parts -> roles, and parts -> theme for ``Color``; theme imports imgui_bundle at
    # the top because it is the palette and the style setter in one module.
    "pptmstr.ui.typeset.parts": "pptmstr.theme",
}

_SAYS_NO_IMGUI = re.compile(r"\bno imgui\b|\bno UI\b|without ImGui", re.IGNORECASE)


@pytest.mark.parametrize("module", sorted(NO_IMGUI))
def test_a_module_that_says_it_imports_no_imgui_still_says_so(module: str) -> None:
    claim = NO_IMGUI[module]
    assert module in modules(), f"{module} is gone: delete its NO_IMGUI entry"
    assert claim.holds(), (
        f"{claim.file} no longer says {claim.words!r}: delete the NO_IMGUI entry for "
        f"{module}, or restore the claim"
    )


@pytest.mark.parametrize("module", sorted(NO_IMGUI))
def test_a_module_that_says_it_imports_no_imgui_imports_none(module: str) -> None:
    found = [edge for edge in imports_of(module) if is_imgui(edge.target)]
    assert (
        not found
    ), f"{module} says it imports no ImGui ({NO_IMGUI[module].file}) and imports " + ", ".join(
        f"{edge.target} ({edge.kind}, line {edge.line})" for edge in found
    )


def _first_imgui_importers(module: str) -> set[str]:
    """
    The modules eagerly reachable from ``module`` that import ImGui eagerly
    themselves: what importing ``module`` would bring ImGui in through.
    """
    eager: dict[str, set[str]] = {}
    for edge in imports():
        if edge.kind == "eager":
            eager.setdefault(edge.source, set()).add(edge.target)
    seen, todo, importers = {module}, [module], set()
    while todo:
        current = todo.pop()
        for target in eager.get(current, ()):
            if is_imgui(target):
                importers.add(current)
            elif target in modules() and target not in seen:
                seen.add(target)
                todo.append(target)
    return importers


@pytest.mark.parametrize("module", sorted(NO_IMGUI))
def test_importing_a_module_that_says_it_imports_no_imgui_brings_none_in(module: str) -> None:
    importers = _first_imgui_importers(module)
    allowed = NO_IMGUI_REACHED_THROUGH.get(module)
    if allowed is None:
        assert not importers, (
            f"importing {module} imports ImGui through {sorted(importers)}, and "
            f"{NO_IMGUI[module].file} says it imports none"
        )
    else:
        assert importers == {allowed}, (
            f"NO_IMGUI_REACHED_THROUGH says {module} reaches ImGui through {allowed}; "
            f"it reaches it through {sorted(importers) or 'nothing'}. If nothing, delete "
            f"the entry."
        )


def test_no_module_says_it_imports_no_imgui_unchecked() -> None:
    """
    A claim added to a docstring is held to from the day it is written.
    """
    saying = {
        module
        for module, path in modules().items()
        if _SAYS_NO_IMGUI.search(" ".join(docstring_of(path).split()))
    }
    unchecked = saying - set(NO_IMGUI) - set(NOT_A_MODULE_CLAIM)
    assert not unchecked, (
        f"{sorted(unchecked)} say in their docstring that they import no ImGui: add "
        f"them to NO_IMGUI, or to NOT_A_MODULE_CLAIM with the reason"
    )
    stale = set(NOT_A_MODULE_CLAIM) - saying
    assert not stale, f"{sorted(stale)} no longer say it: delete their NOT_A_MODULE_CLAIM entry"


# -- 2. store imports nothing that reads the disk ----------------------------------

# Calls that read a file or a directory. Writing is not the rule's concern, but every
# writer here also reads or opens through one of these, so a writer is caught too.
# ``?.read_text`` and ``?.read_bytes`` are ``Path``'s; an attribute call on anything
# else named so is counted as well, which errs toward calling a module a reader.
#
# ``ondisk``'s functions are the disk calls a module makes without naming ``os``, so
# each is here by its absolute name: a module whose only disk call is one of them is
# a reader all the same. Only by that name, because ``?.replace`` would be every
# ``str.replace``. ``test_every_function_ondisk_offers_is_a_disk_call`` holds the
# list to ``ondisk`` itself.
_ONDISK = "pptmstr.ondisk"
_ONDISK_CALLS = frozenset(
    {
        f"{_ONDISK}.read_regular",
        f"{_ONDISK}.create_once",
        f"{_ONDISK}.replace",
        f"{_ONDISK}.sync_directory",
    }
)
_DISK_READS = frozenset(
    {
        "open",
        "io.open",
        "os.open",
        "os.fdopen",
        "os.listdir",
        "os.scandir",
        "os.walk",
        "?.read_text",
        "?.read_bytes",
        "?.iterdir",
        *_ONDISK_CALLS,
    }
)


def _reads_disk(call: Call) -> bool:
    if call.dotted in _DISK_READS:
        return True
    return "?." + call.dotted.rpartition(".")[2] in _DISK_READS


@cache
def disk_readers() -> frozenset[str]:
    return frozenset(
        module for module in modules() if any(_reads_disk(call) for call in calls_of(module))
    )


# Calls that touch the filesystem without reading a file: each asks the disk where a
# path leads or whether it is there, so what it answers depends on the machine and
# not on the arguments. The store rule counts these as well as ``_DISK_READS``; the
# other rules count reads alone. ``settings.config_dir`` is here by its absolute
# name, since it is the one place the configuration directory is located.
#
# ``resolve`` is ``Path.resolve``, which takes no positional argument. Every other
# ``resolve`` in the tree -- ``Bridge.resolve`` and ``ui.review.resolve``, each called
# with the approval it settles, and ``figure_host``'s local ``resolve`` -- is called
# with one, or by a bare name, so only an attribute call with none counts.
_DISK_TOUCHES = frozenset(
    {
        "?.realpath",
        "?.stat",
        "?.exists",
        "?.is_dir",
        "pptmstr.settings.config_dir",
        f"{_ONDISK}.read_regular",
    }
)


def _touches_disk(call: Call) -> bool:
    if call.dotted in _DISK_TOUCHES:
        return True
    name = call.dotted.rpartition(".")[2]
    if name == "resolve":
        return "." in call.dotted and call.positional == 0
    return "." in call.dotted and "?." + name in _DISK_TOUCHES


@cache
def disk_touchers() -> frozenset[str]:
    return frozenset(
        module for module in modules() if any(_touches_disk(call) for call in calls_of(module))
    )


STORE = "pptmstr.store"


def in_store(module: str) -> bool:
    """
    ``store`` and, once it is a package, every module in it: the rule is the store's,
    whichever of its modules holds the import.
    """
    return module == STORE or module.startswith(STORE + ".")


def _walk(
    edges: tuple[Import, ...], known: frozenset[str], inside: Callable[[str], bool]
) -> Iterator[tuple[str, Import, list[str]]]:
    """
    Every import that the modules ``inside`` names can run, on import or through a
    call, as ``(importer, edge, path to the importer)``, breadth first from those
    modules.

    An inside module's own imports count whatever their kind: one importing a
    module inside a function or under ``TYPE_CHECKING`` still imports it. Beyond
    them eager and lazy edges are followed: a lazy import runs when its function
    is called, and the inside modules call into what they import, so a disk read
    behind a function-local import is still theirs. Only a ``TYPE_CHECKING``
    import elsewhere is left out, since it never runs.
    """
    out: dict[str, list[Import]] = {}
    for edge in edges:
        if edge.target in known and (inside(edge.source) or edge.kind != "typing"):
            out.setdefault(edge.source, []).append(edge)
    roots = sorted(module for module in known if inside(module))
    path: dict[str, list[str]] = {module: [module] for module in roots}
    todo = list(roots)
    while todo:
        current = todo.pop(0)
        for edge in sorted(out.get(current, ()), key=lambda e: (e.target, e.line)):
            yield current, edge, path[current]
            if edge.target not in path:
                step = f"{edge.target} ({edge.source}:{edge.line}, {edge.kind})"
                path[edge.target] = [*path[current], step]
                todo.append(edge.target)


def disk_edges(
    edges: tuple[Import, ...],
    readers: frozenset[str],
    known: frozenset[str],
    inside: Callable[[str], bool],
    label: str,
) -> dict[tuple[str, str], list[str]]:
    """
    Every import of a disk reader that importing the modules ``inside`` names runs
    (``_walk``), as ``(reader, importer)``, with a path to that import. The walk
    goes on through a reader, so a second reader behind the first is named too. An
    importer inside is named ``label``, so moving an import between those modules
    changes no key.
    """
    found: dict[tuple[str, str], list[str]] = {}
    for current, edge, path in _walk(edges, known, inside):
        if edge.target in readers:
            importer = label if inside(current) else current
            step = f"{edge.target} ({edge.source}:{edge.line}, {edge.kind})"
            found.setdefault((edge.target, importer), [*path, step])
    return found


def reached(
    edges: tuple[Import, ...], known: frozenset[str], inside: Callable[[str], bool]
) -> frozenset[str]:
    """
    The modules ``inside`` names and every module importing them runs (``_walk``).
    """
    found = {module for module in known if inside(module)}
    found.update(edge.target for _, edge, _ in _walk(edges, known, inside))
    return frozenset(found)


def store_disk_edges(
    edges: tuple[Import, ...], readers: frozenset[str], known: frozenset[str]
) -> dict[tuple[str, str], list[str]]:
    """
    Every import of a disk reader that importing the store runs, from whichever of
    its modules (``disk_edges``); the store's modules are named ``STORE``.
    """
    return disk_edges(edges, readers, known, in_store, STORE)


# Every import of a disk reader or toucher that importing the store runs, keyed by the
# reader and the module that imports it (``store_disk_edges``), with why it is there
# and the design that removes it. The rule is stated in
# planning/2026-09-01-the-gates-measurement-outlives-the-approval.md:55-57 ("store.py
# imports board, effects, intents and model and does not import the shell module that
# reads disk"). There are none: of the gate, the store imports ``gate.policy``,
# ``gate.tools`` and ``gate.rows``, and the row's records mark is measured in
# ``gate.park`` and handed to ``gate.rows.compose``.
STORE_DISK_EXCEPTIONS: dict[tuple[str, str], str] = {}


def test_the_disk_scan_sees_a_reader_and_a_pure_module() -> None:
    readers = disk_readers()
    assert "pptmstr.sessionfile" in readers
    assert "pptmstr.model" not in readers
    # ondisk's only read is ``os.open``; journal.files reaches it only by a relative
    # ``from .. import ondisk``, which the import scan resolves.
    assert "pptmstr.ondisk" in readers
    assert any(
        edge.source == "pptmstr.journal.files" and edge.target == "pptmstr.ondisk"
        for edge in imports()
    )
    # The journal's two halves: the disk half reads, and the board log, which holds
    # the store's restore records, and the codec beneath it do not.
    assert "pptmstr.journal.files" in readers
    assert "pptmstr.journal.codec" not in readers
    assert "pptmstr.boardlog" not in readers


@pytest.mark.parametrize(
    "source",
    [
        "from .ondisk import create_once\ndef w(): create_once(d, ('a',), b'')\n",
        "from . import ondisk\ndef w(): ondisk.replace(p, b'', durable=False)\n",
        "from .ondisk import sync_directory as flush\ndef w(): flush(d)\n",
        "from pptmstr.ondisk import read_regular\ndef w(): read_regular(p, 1)\n",
    ],
)
def test_a_write_through_ondisk_is_a_disk_call_however_it_is_imported(source: str) -> None:
    assert any(_reads_disk(call) for call in calls(ast.parse(source), "pptmstr.x"))


def test_a_method_named_like_an_ondisk_function_is_not_a_disk_call() -> None:
    source = "def w(s): return s.replace('a', 'b'), replace(s), create_once(s)\n"
    assert not any(_reads_disk(call) for call in calls(ast.parse(source), "pptmstr.x"))


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\ndef w(p): return Path(p).resolve()\n",
        "def w(base, p): return (base / p).resolve(strict=True)\n",
        "def w(bold): return bold.resolve()\n",
        "import os\ndef w(p): return os.path.realpath(p)\n",
        "import os\ndef w(p): return os.stat(p)\n",
        "def w(p): return p.exists() or p.is_dir()\n",
        "from . import settings\ndef w(): return settings.config_dir()\n",
        "from .settings import config_dir\ndef w(): return config_dir()\n",
        "from .ondisk import read_regular\ndef w(p): return read_regular(p, 1)\n",
    ],
)
def test_a_call_that_asks_where_a_path_leads_touches_the_disk(source: str) -> None:
    assert any(_touches_disk(call) for call in calls(ast.parse(source), "pptmstr.x"))


@pytest.mark.parametrize(
    "source",
    [
        "def w(bridge, rid): return bridge.resolve(rid, True)\n",
        "from .ui import review\ndef w(b, s, p): return review.resolve(b, s, p)\n",
        "def w(p, q, r): return resolve(p, q, r)\n",
        "def w(): return exists() or stat() or is_dir()\n",
    ],
)
def test_a_call_named_like_a_path_question_is_not_one(source: str) -> None:
    """
    ``Bridge.resolve`` settles an approval, ``ui.review.resolve`` asks it to, and
    each is called with the approval: a ``?.resolve`` that matched them would put
    the bridge among the store's disk touchers.
    """
    assert not any(_touches_disk(call) for call in calls(ast.parse(source), "pptmstr.ui.x"))


def test_the_store_rule_counts_resolve_where_the_tree_resolves_a_path() -> None:
    """
    A path the tree resolves is counted, and the approval ``ui.review`` resolves is
    not.
    """
    touchers = disk_touchers()
    assert {"pptmstr.plugins", "pptmstr.tree", "pptmstr.version"} <= touchers
    assert "pptmstr.ui.review" not in touchers
    assert "pptmstr.ui.review" not in disk_readers()


def test_every_function_ondisk_offers_is_a_disk_call() -> None:
    offered = {
        f"{_ONDISK}.{node.name}"
        for node in tree_of(_ONDISK).body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }
    assert offered == _ONDISK_CALLS


def test_every_module_that_imports_ondisk_is_seen_calling_it() -> None:
    """
    By its ``ondisk`` call alone, not by the other reads it also makes: a writer
    whose last ``open`` moves into ``ondisk`` must stay a reader.
    """
    importers = {edge.source for edge in imports() if edge.target == _ONDISK} - {_ONDISK}
    assert importers
    unseen = sorted(
        module
        for module in importers
        if not any(call.dotted in _ONDISK_CALLS for call in calls_of(module))
    )
    assert not unseen, f"{unseen} import ondisk and the scan sees none of its calls"


def test_the_store_walk_follows_what_importing_the_store_runs() -> None:
    def edge(source: str, target: str, kind: Kind = "eager") -> Import:
        return Import(source, target, kind, 1)

    known = frozenset(
        {STORE, "pptmstr.store.arms", "pptmstr.a", "pptmstr.b", "pptmstr.r", "pptmstr.s"}
    )
    found = store_disk_edges(
        (
            edge(STORE, "pptmstr.store.arms"),
            edge("pptmstr.store.arms", "pptmstr.a", "lazy"),
            edge("pptmstr.a", "pptmstr.r"),
            edge("pptmstr.r", "pptmstr.s"),
            edge(STORE, "pptmstr.b"),
            edge("pptmstr.b", "pptmstr.s", "lazy"),
            edge("pptmstr.b", "pptmstr.r", "typing"),
            edge("pptmstr.store.arms", "pptmstr.s", "typing"),
        ),
        readers=frozenset({"pptmstr.r", "pptmstr.s"}),
        known=known,
    )
    assert set(found) == {
        ("pptmstr.r", "pptmstr.a"),
        ("pptmstr.s", "pptmstr.r"),
        ("pptmstr.s", "pptmstr.b"),
        ("pptmstr.s", STORE),
    }
    assert found[("pptmstr.r", "pptmstr.a")][0] == "pptmstr.store.arms"


def test_store_reaches_no_module_that_reads_the_disk() -> None:
    readers = disk_readers() | disk_touchers()
    reading = sorted(module for module in modules() if in_store(module) and module in readers)
    assert not reading, f"{reading} read the disk themselves"
    found = store_disk_edges(imports(), readers, frozenset(modules()))
    new = set(found) - set(STORE_DISK_EXCEPTIONS)
    stale = set(STORE_DISK_EXCEPTIONS) - set(found)
    assert not new, "importing the store reaches a disk reader: " + "; ".join(
        f"{reader} imported by {importer}, by " + " -> ".join(found[(reader, importer)])
        for reader, importer in sorted(new)
    )
    assert not stale, "no longer reached, delete from STORE_DISK_EXCEPTIONS: " + ", ".join(
        f"{reader} imported by {importer}" for reader, importer in sorted(stale)
    )


def test_only_the_store_imports_a_module_of_the_store() -> None:
    """
    ``_apply`` is the audit point because every step goes through it; a module
    outside the store calling one of its parts would be a step that does not.
    """
    reaching = sorted(
        f"{edge.source}:{edge.line} imports {edge.target}"
        for edge in imports()
        if edge.target.startswith(STORE + ".") and not in_store(edge.source)
    )
    assert not reaching, "; ".join(reaching)


def test_the_store_is_a_package_of_modules() -> None:
    """
    The rule above is vacuous over a store with no modules.
    """
    assert {module for module in modules() if module.startswith(STORE + ".")} >= {
        f"{STORE}.tasks",
        f"{STORE}.waking",
    }


# -- 3. nothing outside ui imports ui ----------------------------------------------

# Build the window and run it; every pane is theirs to call.
COMPOSITION_ROOTS = frozenset({"pptmstr.app", "pptmstr.__main__"})

# Each non-ui -> ui edge in the tree, with its kind and its reason. There are none:
# what a module beneath ui needs of it (the faces a figure is set in, the text
# library's fonts) is handed down by a composition root.
NON_UI_IMPORTS_UI: dict[tuple[str, str, Kind], str] = {}


def test_the_composition_roots_exist() -> None:
    assert COMPOSITION_ROOTS <= set(modules())


def test_no_module_outside_ui_imports_ui() -> None:
    found = {
        (edge.source, edge.target, edge.kind)
        for edge in imports()
        if not is_ui(edge.source) and edge.source not in COMPOSITION_ROOTS and is_ui(edge.target)
    }
    new = found - set(NON_UI_IMPORTS_UI)
    stale = set(NON_UI_IMPORTS_UI) - found
    assert not new, "outside pptmstr/ui, importing ui: " + ", ".join(
        f"{source} -> {target} ({kind})" for source, target, kind in sorted(new)
    )
    assert not stale, "no longer imported, delete from NON_UI_IMPORTS_UI: " + ", ".join(
        f"{source} -> {target} ({kind})" for source, target, kind in sorted(stale)
    )


# -- 4. pure modules read no clock -------------------------------------------------

_CLOCKS = frozenset(
    {
        "time.time",
        "time.time_ns",
        "time.monotonic",
        "time.monotonic_ns",
        "time.perf_counter",
        "time.perf_counter_ns",
        "datetime.datetime.now",
        "datetime.datetime.utcnow",
        "datetime.datetime.today",
        "datetime.date.today",
    }
)

_STYLE_REDUCER = Claim("STYLE.md", "nothing reads a clock, nothing performs IO")

# Every module the docstrings or STYLE.md call pure as a whole, with the words.
PURE: dict[str, Claim] = {
    # STYLE.md:41-53: the records, the intents and the effects are the reducer's
    # inputs and outputs; ``now`` is a parameter.
    "pptmstr.model": _STYLE_REDUCER,
    "pptmstr.intents": _STYLE_REDUCER,
    "pptmstr.effects": Claim("pptmstr/effects.py", "``_apply`` is a pure function"),
    # boardfacts.py:2
    "pptmstr.boardfacts": Claim("pptmstr/boardfacts.py", "Pure: two snapshots and the intent"),
    # addresses.py:15
    "pptmstr.addresses": Claim(
        "pptmstr/addresses.py",
        "Pure: every function here reads one ``Snapshot`` and nothing else -- no clock, no IO",
    ),
    # board/graph.py:5
    "pptmstr.board.graph": Claim(
        "pptmstr/board/graph.py", "Every function here is pure over one ``Snapshot``"
    ),
    # board/digest.py:8-9
    "pptmstr.board.digest": Claim(
        "pptmstr/board/digest.py",
        "``digest`` is a pure function of a snapshot, a session and a point",
    ),
    # command.py:5
    "pptmstr.command": Claim(
        "pptmstr/command.py", "Nothing here performs IO, reads a clock, or draws"
    ),
    # commandtext.py:4
    "pptmstr.commandtext": NO_IMGUI["pptmstr.commandtext"],
    # shellscan.py:4
    "pptmstr.shellscan": NO_IMGUI["pptmstr.shellscan"],
    # gate/sandbox.py:6
    "pptmstr.gate.sandbox": Claim("pptmstr/gate/sandbox.py", "no IO, no clock, no launch"),
    # gate/rows.py:4
    "pptmstr.gate.rows": NO_IMGUI["pptmstr.gate.rows"],
    # replies.py:11
    "pptmstr.replies": Claim("pptmstr/replies.py", "Pure: no SDK, no clock, no IO"),
    # board/text.py
    "pptmstr.board.text": Claim("pptmstr/board/text.py", "Pure: no SDK, no clock, no IO"),
    # board/graphtext.py
    "pptmstr.board.graphtext": Claim("pptmstr/board/graphtext.py", "Pure: no SDK, no clock, no IO"),
    # board/digesttext.py
    "pptmstr.board.digesttext": Claim(
        "pptmstr/board/digesttext.py", "Pure: no SDK, no clock, no IO"
    ),
    # channels/text.py
    "pptmstr.channels.text": Claim("pptmstr/channels/text.py", "Pure: no SDK, no clock, no IO"),
    # decisiontext.py
    "pptmstr.decisiontext": Claim("pptmstr/decisiontext.py", "Pure: no SDK, no clock, no IO"),
    # waketext.py
    "pptmstr.waketext": Claim("pptmstr/waketext.py", "Pure: no SDK, no clock, no IO"),
    # concerntext.py
    "pptmstr.concerntext": Claim("pptmstr/concerntext.py", "Pure: no SDK, no clock, no IO"),
    # naming.py:5
    "pptmstr.naming": Claim(
        "pptmstr/naming.py", "Pure: no clock, no IO, and no import from pptmstr"
    ),
    # jsonfields.py:4
    "pptmstr.jsonfields": Claim("pptmstr/jsonfields.py", "Pure: no IO, no clock, no state"),
    # journal/codec.py:2
    "pptmstr.journal.codec": Claim("pptmstr/journal/codec.py", "Pure: no IO, no clock"),
    # boardlog.py:97
    "pptmstr.boardlog": Claim(
        "pptmstr/boardlog.py", "**Pure: no IO, no clock, and nothing here is in the snapshot.**"
    ),
    # channels/facts.py:5
    "pptmstr.channels.facts": Claim(
        "pptmstr/channels/facts.py", "Pure: no disk, no clock, no thread"
    ),
    # channels/records.py:9
    "pptmstr.channels.records": Claim(
        "pptmstr/channels/records.py", "It is pure: no disk, no clock, no entropy"
    ),
    # ui/clip.py:4
    "pptmstr.ui.clip": Claim("pptmstr/ui/clip.py", "Pure: one function of a string and a limit"),
    # figures/__init__.py:4
    "pptmstr.figures.displaylist": NO_IMGUI["pptmstr.figures.displaylist"],
    # figures/sanitise.py:5-6
    "pptmstr.figures.sanitise": NO_IMGUI["pptmstr.figures.sanitise"],
    # ui/splash.py:11
    "pptmstr.ui.splash": NO_IMGUI["pptmstr.ui.splash"],
    # ui/typeset/parts.py:4
    "pptmstr.ui.typeset.parts": NO_IMGUI["pptmstr.ui.typeset.parts"],
    # The modules of the store's step, each saying so in its docstring.
    **{
        f"{STORE}.{name}": Claim(f"pptmstr/store/{name}.py", "Pure: no SDK, no clock, no IO")
        for name in (
            "approvals",
            "commands",
            "decisions",
            "mail",
            "nodes",
            "owing",
            "peers",
            "restore",
            "tasks",
            "waking",
        )
    },
}

# store holds the reducer and the Store cell around it. STYLE.md:49-50: ``now`` is a
# parameter so the core cannot read a clock; the entry point supplies it, and this is
# the one function that does, by its module and name.
STORE_CLOCK_SITES = frozenset({("pptmstr.store", "_clock")})


@pytest.mark.parametrize("module", sorted(PURE))
def test_a_module_called_pure_is_still_called_pure(module: str) -> None:
    claim = PURE[module]
    assert module in modules(), f"{module} is gone: delete its PURE entry"
    assert (
        claim.holds()
    ), f"{claim.file} no longer says {claim.words!r}: delete the PURE entry for {module}"


@pytest.mark.parametrize("module", sorted(PURE))
def test_a_pure_module_reads_no_clock(module: str) -> None:
    found = [call for call in calls_of(module) if call.dotted in _CLOCKS]
    assert not found, f"{module} is pure ({PURE[module].file}) and reads a clock: " + ", ".join(
        f"{call.dotted} in {call.function} (line {call.line})" for call in found
    )


def test_store_reads_a_clock_only_at_its_entry_point() -> None:
    sites = {
        (module, call.function)
        for module in modules()
        if in_store(module)
        for call in calls_of(module)
        if call.dotted in _CLOCKS
    }
    new = sites - STORE_CLOCK_SITES
    stale = STORE_CLOCK_SITES - sites
    assert not new, f"store reads a clock in {sorted(new)}; take ``now`` as a parameter"
    assert not stale, f"store no longer reads a clock in {sorted(stale)}: delete it"


# -- 5. the bus and its command tools import neither of each other ----------------


def test_the_bus_and_commandbus_import_nothing_of_each_other() -> None:
    """
    Both lay their replies out with ``replies``, and ``build_server`` is handed
    ``commandbus.TOOLS`` by its caller. An edge of any kind, a ``TYPE_CHECKING``
    one included, is the cycle back.
    """
    pair = {"pptmstr.bus", "pptmstr.commandbus"}
    found = [
        edge
        for edge in imports()
        if edge.source in pair and edge.target in pair and edge.source != edge.target
    ]
    assert not found, ", ".join(
        f"{edge.source} -> {edge.target} ({edge.kind}, line {edge.line})" for edge in found
    )


# -- 6. naming imports nothing of pptmstr ------------------------------------------

BOARD = "pptmstr.board"


def test_naming_imports_nothing_from_pptmstr() -> None:
    """
    What lets the driver and the board log name agents without importing
    ``board``. ``board.rows`` reaches ``naming`` through ``addresses``, and the
    board log and the store's restore import it, so one edge into the package of
    any kind is a way back.
    """
    found = [edge for edge in imports_of("pptmstr.naming") if edge.target.startswith("pptmstr")]
    assert not found, ", ".join(f"{edge.target} ({edge.kind}, line {edge.line})" for edge in found)


def _is_board(target: str) -> bool:
    return target == BOARD or target.startswith(BOARD + ".")


# The names that left ``board`` for the module that holds them, by that module.
MOVED_OUT_OF_BOARD: dict[str, frozenset[str]] = {
    "pptmstr.naming": frozenset({"LEAD", "ROOT_NAMES", "address_for", "reserved"}),
    "pptmstr.addresses": frozenset(
        {
            "role_name",
            "_role_within_session",
            "address_in_session",
            "restored_table",
            "_addresses",
            "UNKNOWN",
            "FOREIGN",
            "OPERATOR",
        }
    ),
}


def test_nothing_reaches_a_moved_name_through_board() -> None:
    """
    No shim: a name ``naming`` or ``addresses`` holds is imported from it, never
    from the ``board`` package or any module in it. Each name is checked to be
    defined where the table says, so a rename there cannot leave this vacuous.
    """
    for home, names in MOVED_OUT_OF_BOARD.items():
        assert names <= _defined_at_top(
            home
        ), f"{home} no longer defines {names - _defined_at_top(home)}"
    found = [
        f"{module} line {node.lineno}: {alias.name} (held by {home})"
        for module in modules()
        for node in ast.walk(tree_of(module))
        if isinstance(node, ast.ImportFrom)
        and _is_board(_from(module, modules()[module].name == "__init__.py", node))
        for alias in node.names
        for home, names in MOVED_OUT_OF_BOARD.items()
        if alias.name in names
    ]
    assert not found, "imports a moved name through board: " + ", ".join(found)


# -- 7. serialworker imports nothing of pptmstr ------------------------------------


def test_serialworker_imports_nothing_from_pptmstr() -> None:
    """
    The worker is the loop two journal writers share and knows nothing of either:
    no path, no log, no record type. An edge into the package of any kind is
    domain knowledge moving into the loop.
    """
    found = [
        edge for edge in imports_of("pptmstr.serialworker") if edge.target.startswith("pptmstr")
    ]
    assert not found, ", ".join(f"{edge.target} ({edge.kind}, line {edge.line})" for edge in found)


# -- 8. ids owns minting, and imports nothing of pptmstr ---------------------------

IDS = "pptmstr.ids"
_ENTROPY = frozenset({"uuid", "secrets"})

# Every module but ``ids`` that imports an entropy source, with why. Each line
# goes when its mints move (planning/2026-11-07-channels-and-ids-have-one-owner-
# each.md, steps 1 and 8).
MINTS_ELSEWHERE: dict[tuple[str, str], str] = {
    # ``mint_tag`` is a capability secret for the runner protocol, not an id.
    ("pptmstr.commandrun", "secrets"): "a secret, not an id",
}


def test_ids_imports_nothing_from_pptmstr() -> None:
    """
    ``model`` reads id shapes from ``ids``, so an edge back of any kind is a cycle.
    """
    found = [edge for edge in imports_of(IDS) if edge.target.startswith("pptmstr")]
    assert not found, ", ".join(f"{edge.target} ({edge.kind}, line {edge.line})" for edge in found)


def test_ids_reads_no_clock() -> None:
    """
    A command's stamp is the caller's wall-clock reading, handed in: the same one
    the command's files are stamped with.
    """
    found = [call for call in calls_of(IDS) if call.dotted in _CLOCKS]
    assert not found, ", ".join(f"{call.dotted} in {call.function}" for call in found)


def test_only_ids_imports_an_entropy_source() -> None:
    seen = {
        (edge.source, edge.target.split(".")[0])
        for edge in imports()
        if edge.target.split(".")[0] in _ENTROPY and edge.source != IDS
    }
    new = seen - MINTS_ELSEWHERE.keys()
    stale = MINTS_ELSEWHERE.keys() - seen
    assert not new, "mint through pptmstr.ids instead: " + ", ".join(
        f"{source} imports {target}" for source, target in sorted(new)
    )
    assert not stale, "no longer imported, delete from MINTS_ELSEWHERE: " + ", ".join(
        f"{source} -> {target}" for source, target in sorted(stale)
    )
    assert any(edge.target == "uuid" for edge in imports_of(IDS)), "ids no longer mints"


# -- 9. channels: one package, one module per requirement --------------------------

CHANNELS = "pptmstr.channels"
RECORDS = "pptmstr.channels.records"
CHANNEL_LOG = "pptmstr.channels.log"


def test_the_channels_package_imports_nothing() -> None:
    """
    ``model`` imports ``channels.records``, which runs ``channels/__init__`` first.
    An import there -- of ``log`` above all -- would have ``model`` reach the disk
    and a thread through it, so the package's ``__init__`` is a docstring only.
    """
    found = imports_of(CHANNELS)
    assert not found, ", ".join(f"{edge.target} ({edge.kind}, line {edge.line})" for edge in found)


def test_channel_records_import_nothing_from_pptmstr() -> None:
    """
    ``model`` imports ``records``, so an edge into the package of any kind is a
    cycle back through ``model`` or a disk reader under it.
    """
    found = [edge for edge in imports_of(RECORDS) if edge.target.startswith("pptmstr")]
    assert not found, ", ".join(f"{edge.target} ({edge.kind}, line {edge.line})" for edge in found)


def test_only_app_imports_the_channel_log() -> None:
    """
    The log holds the disk and a thread; ``app`` is the composition root that
    restores from it at startup and hands it the store's outcomes.
    """
    importers = {
        edge.source
        for edge in imports()
        if edge.target == CHANNEL_LOG and not edge.source.startswith(CHANNEL_LOG)
    }
    assert importers == {"pptmstr.app"}, sorted(importers)


CHANNEL_FACTS = "pptmstr.channels.facts"


def _is_channel_facts(module: str) -> bool:
    return module == CHANNEL_FACTS


def test_channel_facts_reach_no_disk_reader() -> None:
    """
    ``facts`` is the half of a link's log with no disk. Importing it runs
    ``intents`` and ``model``, which import the gate's leaves and no reader, so
    any edge to a reader -- ``facts`` importing ``journal.files``, ``settings`` or
    ``log`` in any form, or ``model`` importing ``gate.park`` -- fails.
    """
    readers = disk_readers()
    assert CHANNEL_FACTS in modules() and CHANNEL_FACTS not in readers
    found = disk_edges(imports(), readers, frozenset(modules()), _is_channel_facts, CHANNEL_FACTS)
    assert not found, "importing channels.facts reaches a disk reader: " + "; ".join(
        f"{reader} imported by {importer}, by " + " -> ".join(found[(reader, importer)])
        for reader, importer in sorted(found)
    )


# Calls that start a thread, by the name they resolve to.
_THREAD_STARTS = frozenset(
    {
        "threading.Thread",
        "threading.Timer",
        "_thread.start_new_thread",
        "concurrent.futures.ThreadPoolExecutor",
        "pptmstr.serialworker.SerialWorker",
    }
)


def test_nothing_importing_channel_facts_runs_starts_a_thread() -> None:
    """
    The appender's thread is ``log``'s (``ChannelWriter`` through
    ``serialworker``). Neither is reached from ``facts``, and no module it reaches
    calls a thread into being.
    """
    reach = reached(imports(), frozenset(modules()), _is_channel_facts)
    assert not reach & {CHANNEL_LOG, "pptmstr.serialworker"}, sorted(reach)
    found = [
        f"{module} line {call.line}: {call.dotted} in {call.function}"
        for module in sorted(reach)
        for call in calls_of(module)
        if call.dotted in _THREAD_STARTS or call.dotted.rpartition(".")[2] == "Thread"
    ]
    assert not found, "importing channels.facts reaches a thread: " + ", ".join(found)


def _defined_at_top(module: str) -> frozenset[str]:
    names: set[str] = set()
    for node in tree_of(module).body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return frozenset(names)


def test_nothing_reaches_a_channel_record_through_another_module() -> None:
    """
    No shim: ``model``, ``effects`` and ``store`` import the records they use, and
    every other module imports a record from ``records`` too, never through them.
    """
    defined = _defined_at_top(RECORDS)
    assert {"Channel", "ChannelId", "Charter", "PeerIdentity"} <= defined
    found = [
        f"{module} line {node.lineno}: {alias.name} from {_from(module, False, node)}"
        for module in modules()
        for node in ast.walk(tree_of(module))
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name in defined
        and _from(module, modules()[module].name == "__init__.py", node) != RECORDS
    ]
    assert not found, "imports a channel record through another module: " + ", ".join(found)


# -- 10. board: the read side, and words that need no snapshot ---------------------

BOARD_WORDS = "pptmstr.board.words"


def test_the_board_package_imports_nothing() -> None:
    """
    ``gate.rows``' queue rows, the bus and the board's text import
    ``board.words``, and importing it runs ``board/__init__`` first. An import
    there would bring ``rows`` -- and ``model`` -- to each of them with the words,
    so the package's ``__init__`` is a docstring only.
    """
    found = imports_of(BOARD)
    assert not found, ", ".join(f"{edge.target} ({edge.kind}, line {edge.line})" for edge in found)


def test_board_words_import_nothing_from_pptmstr_when_they_run() -> None:
    """
    ``Basis`` is named in an annotation and read as ``.value``, so it is imported
    under ``TYPE_CHECKING`` only. An eager or lazy edge into the package is one
    that runs, and every importer of the words would run it too.
    """
    found = [
        edge
        for edge in imports_of(BOARD_WORDS)
        if edge.target.startswith("pptmstr") and edge.kind != "typing"
    ]
    assert not found, ", ".join(f"{edge.target} ({edge.kind}, line {edge.line})" for edge in found)


def test_nothing_reaches_a_board_word_through_another_module() -> None:
    """
    No shim: every module imports the board's words from ``words``, never through
    ``rows`` or a module that imported them.
    """
    defined = _defined_at_top(BOARD_WORDS)
    assert {"basis_label", "property_label", "not_run_label", "incident_marker"} <= defined
    found = [
        f"{module} line {node.lineno}: {alias.name} from {_from(module, False, node)}"
        for module in modules()
        for node in ast.walk(tree_of(module))
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name in defined
        and _from(module, modules()[module].name == "__init__.py", node) != BOARD_WORDS
    ]
    assert not found, "imports a board word through another module: " + ", ".join(found)


# -- 12. gate: the leaves the records import -------------------------------------

GATE_LEAVES = ("pptmstr.gate.policy", "pptmstr.gate.tools", "pptmstr.gate.diff")

# gate/__init__.py: why ``model`` may import them.
_GATE_LEAVES_CLAIM = Claim("pptmstr/gate/__init__.py", "Each imports nothing from pptmstr")


def test_the_gate_package_still_says_its_leaves_import_nothing() -> None:
    assert _GATE_LEAVES_CLAIM.holds(), (
        f"{_GATE_LEAVES_CLAIM.file} no longer says {_GATE_LEAVES_CLAIM.words!r}: restore it,"
        f" or delete the claim with the rule it states"
    )


@pytest.mark.parametrize("module", GATE_LEAVES)
def test_a_gate_leaf_imports_nothing_from_pptmstr(module: str) -> None:
    """
    ``model``, ``intents``, ``templates`` and the store import these, so an edge
    into the package of any kind, a ``TYPE_CHECKING`` one included, is a way back
    into ``model``.
    """
    assert module in modules(), f"{module} is gone: delete it from GATE_LEAVES"
    found = [edge for edge in imports_of(module) if edge.target.startswith("pptmstr")]
    assert not found, ", ".join(f"{edge.target} ({edge.kind}, line {edge.line})" for edge in found)


def test_the_gate_package_imports_nothing() -> None:
    """
    Importing a leaf runs ``gate/__init__`` first, so an import there is an
    import of every leaf.
    """
    found = imports_of("pptmstr.gate")
    assert not found, ", ".join(f"{edge.target} ({edge.kind}, line {edge.line})" for edge in found)


@pytest.mark.parametrize("home", GATE_LEAVES)
def test_nothing_reaches_a_gate_leaf_name_through_another_module(home: str) -> None:
    """
    No shim: no module re-exports a leaf's name, so every module imports
    ``Policy``, ``DiffText``, the tool tables and the rest from the leaf that
    defines them.
    """
    defined = _defined_at_top(home)
    assert defined, f"{home} defines nothing"
    found = [
        f"{module} line {node.lineno}: {alias.name} from {_from(module, False, node)}"
        for module in modules()
        for node in ast.walk(tree_of(module))
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name in defined
        and _from(module, modules()[module].name == "__init__.py", node) != home
    ]
    assert not found, f"imports a name {home} defines through another module: " + ", ".join(found)


# -- 13. the one cycle outside ui ----------------------------------------------------

# The import cycle among modules outside ui, over every kind of edge. ``command``
# names ``NodeId`` in annotations and imports it under ``TYPE_CHECKING`` (command.py:28-31),
# and ``model`` imports ``command`` when it runs. Any other cycle, or the edge from
# ``command`` turning eager or lazy, fails.
CYCLE_OUTSIDE_UI = frozenset({"pptmstr.command", "pptmstr.model"})
CYCLE_TYPING_EDGE = ("pptmstr.command", "pptmstr.model")


def strongly_connected(edges: Iterator[tuple[str, str]]) -> list[frozenset[str]]:
    """
    The components of more than one module in the graph ``edges`` draws, by
    Tarjan's algorithm, iteratively so the tree's depth cannot reach the
    recursion limit.
    """
    graph: dict[str, set[str]] = {}
    for source, sink in edges:
        if source != sink:
            graph.setdefault(source, set()).add(sink)
            graph.setdefault(sink, set())
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[frozenset[str]] = []
    for root in sorted(graph):
        if root in index:
            continue
        work = [(root, iter(sorted(graph[root])))]
        index[root] = low[root] = len(index)
        stack.append(root)
        on_stack.add(root)
        while work:
            node, targets = work[-1]
            target = next(targets, None)
            if target is None:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[node])
                if low[node] == index[node]:
                    component: set[str] = set()
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component.add(member)
                        if member == node:
                            break
                    if len(component) > 1:
                        found.append(frozenset(component))
            elif target not in index:
                index[target] = low[target] = len(index)
                stack.append(target)
                on_stack.add(target)
                work.append((target, iter(sorted(graph[target]))))
            elif target in on_stack:
                low[node] = min(low[node], index[target])
    return found


def test_the_cycle_search_finds_each_cycle_and_nothing_else() -> None:
    edges = [
        ("a", "b"),
        ("b", "c"),
        ("c", "a"),
        ("c", "d"),
        ("d", "e"),
        ("e", "d"),
        ("f", "f"),
        ("f", "a"),
    ]
    assert sorted(strongly_connected(iter(edges)), key=sorted) == [
        frozenset({"a", "b", "c"}),
        frozenset({"d", "e"}),
    ]


def test_the_one_cycle_outside_ui_is_command_and_model() -> None:
    """
    Over every kind of edge, since a lazy or ``TYPE_CHECKING`` edge is still
    one module needing another. A cycle wholly inside ``pptmstr/ui`` is not this
    rule's; one through a module outside it is.
    """
    known = modules()
    cycles = [
        component
        for component in strongly_connected(
            (edge.source, edge.target) for edge in imports() if edge.target in known
        )
        if not all(is_ui(module) for module in component)
    ]
    assert cycles == [CYCLE_OUTSIDE_UI], "the import cycles outside ui are " + "; ".join(
        "{" + ", ".join(sorted(component)) + "}" for component in cycles
    )
    source, target = CYCLE_TYPING_EDGE
    kinds = {edge.kind for edge in imports_of(source) if edge.target == target}
    assert kinds == {"typing"}, (
        f"{source} imports {target} as {sorted(kinds)}: only under TYPE_CHECKING does the"
        f" cycle stay out of what runs"
    )
