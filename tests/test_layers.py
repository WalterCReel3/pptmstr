"""
The layer rules the tree states in prose, checked against its imports.

Each rule below was a sentence in a docstring, in ``STYLE.md`` or in a planning
record, and nothing held the code to it (``planning/2026-11-06-modules-are-named-
for-what-they-require.md``, T3, T7 and A7). Six are checked here:

1. A module whose docstring says it imports no ImGui imports no ``imgui_bundle``,
   directly in any form, and does not reach it through the imports that run when
   it is imported.
2. ``store`` imports no module that reads the disk, directly or through the imports
   that run when it is imported, from whichever of its modules.
3. No module outside ``pptmstr/ui`` imports ``pptmstr.ui``, the two composition
   roots aside.
4. A module the docstrings or ``STYLE.md`` call pure calls no clock.
5. ``bus`` and ``commandbus`` import nothing of each other (A6).
6. ``naming`` imports nothing from pptmstr, so it reaches neither ``board`` nor
   ``ui`` (A1).

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
any call not in ``_DISK_READS``.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
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
    if node.level:
        package = module.split(".") if is_package else module.split(".")[:-1]
        base = ".".join(package[: len(package) - (node.level - 1)])
        full = f"{base}.{node.module}" if node.module else base
    else:
        full = node.module or ""
    known = modules()
    return [
        f"{full}.{alias.name}" if f"{full}.{alias.name}" in known else full for alias in node.names
    ]


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


def _aliases(tree: ast.Module) -> dict[str, str]:
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                bound[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return bound


@dataclass(frozen=True)
class Call:
    dotted: str
    function: str
    line: int


def calls(tree: ast.Module) -> Iterator[Call]:
    """
    Every call whose callee is a dotted name, with the innermost enclosing function
    (``<module>`` at the top level).
    """
    aliases = _aliases(tree)

    def visit(node: ast.AST, function: str) -> Iterator[Call]:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if isinstance(node, ast.Call):
            dotted = _dotted(node.func, aliases)
            if dotted is not None:
                yield Call(dotted, function, node.lineno)
            elif isinstance(node.func, ast.Attribute):
                yield Call(f"?.{node.func.attr}", function, node.lineno)
        for child in ast.iter_child_nodes(node):
            yield from visit(child, function)

    yield from visit(tree, "<module>")


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
    # approval.py:4
    "pptmstr.approval": Claim("pptmstr/approval.py", "No SDK and no UI"),
    # board.py:24
    "pptmstr.board": Claim("pptmstr/board.py", "Imports no imgui"),
    # digest.py:67
    "pptmstr.digest": Claim("pptmstr/digest.py", "Imports no imgui and no SDK"),
    # commandtext.py:4
    "pptmstr.commandtext": Claim("pptmstr/commandtext.py", "Pure functions: no UI, no IO"),
    # shellscan.py:4
    "pptmstr.shellscan": Claim("pptmstr/shellscan.py", "Pure functions: no SDK, no UI, no IO"),
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
    }
)


def _reads_disk(call: Call) -> bool:
    if call.dotted in _DISK_READS:
        return True
    return "?." + call.dotted.rpartition(".")[2] in _DISK_READS


@cache
def disk_readers() -> frozenset[str]:
    return frozenset(
        module for module in modules() if any(_reads_disk(call) for call in calls(tree_of(module)))
    )


STORE = "pptmstr.store"


def in_store(module: str) -> bool:
    """
    ``store`` and, once it is a package, every module in it: the rule is the store's,
    whichever of its modules holds the import.
    """
    return module == STORE or module.startswith(STORE + ".")


def store_disk_edges(
    edges: tuple[Import, ...], readers: frozenset[str], known: frozenset[str]
) -> dict[tuple[str, str], list[str]]:
    """
    Every import of a disk reader that importing the store runs, as ``(reader,
    importer)``, with a path from the store to that import.

    A store module's own imports count whatever their kind, as rule 2 always has:
    the store importing a reader inside a function or under ``TYPE_CHECKING`` is
    still the store importing it. Beyond the store only eager edges are followed,
    since a lazy or typing import elsewhere does not run when the store is imported.
    The walk goes on through a reader, so a second reader behind the first is named
    too. An importer inside the store is named ``STORE``, so moving an import
    between store modules changes no key.
    """
    out: dict[str, list[Import]] = {}
    for edge in edges:
        if edge.target in known and (in_store(edge.source) or edge.kind == "eager"):
            out.setdefault(edge.source, []).append(edge)
    roots = sorted(module for module in known if in_store(module))
    path: dict[str, list[str]] = {module: [module] for module in roots}
    todo = list(roots)
    found: dict[tuple[str, str], list[str]] = {}
    while todo:
        current = todo.pop(0)
        for edge in sorted(out.get(current, ()), key=lambda e: (e.target, e.line)):
            step = f"{edge.target} ({edge.source}:{edge.line}, {edge.kind})"
            if edge.target in readers:
                importer = STORE if in_store(current) else current
                found.setdefault((edge.target, importer), [*path[current], step])
            if edge.target not in path:
                path[edge.target] = [*path[current], step]
                todo.append(edge.target)
    return found


# Every import of a disk reader that importing the store runs, keyed by the reader and
# the module that imports it (``store_disk_edges``), with why it is there and the
# design that removes it. The rule is stated in
# planning/2026-09-01-the-gates-measurement-outlives-the-approval.md:55-57 ("store.py
# imports board, effects, intents and model and does not import the shell module that
# reads disk"), and it does not hold: every edge below is eager and predates this test.
# B1 is planning/2026-11-07-the-board-log-is-one-journal-of-two.md, which takes the disk
# out of boardlog; B5 is planning/2026-11-07-board-and-gate-packages.md, which deletes
# approval for gate/ and leaves the store importing only its pure modules.
STORE_DISK_EXCEPTIONS: dict[tuple[str, str], str] = {
    # store.py:35, for Policy, Summary, peer_channel and summary_of.
    ("pptmstr.approval", STORE): "approval's render_diff reads the file a write targets"
    " (``_read``); B5 removes it",
    # intents.py:32, for Policy.
    ("pptmstr.approval", "pptmstr.intents"): "intents carries a Policy; B5 removes it",
    # model.py:28, for DiffText, Policy and peer_channel.
    ("pptmstr.approval", "pptmstr.model"): "model's records carry a Policy and a DiffText;"
    " B5 removes it",
    # templates.py:34, for Policy.
    ("pptmstr.approval", "pptmstr.templates"): "a template names its Policy; B5 removes it",
    # approval.py:29, for config_dir, which approval's records reach resolves.
    ("pptmstr.settings", "pptmstr.approval"): "settings reads and saves settings.json;"
    " B5 removes it with approval",
    # store.py:37, for Board, Party, RestoredConcern, RestoredDecision, RestoredTask.
    ("pptmstr.boardlog", STORE): "the restored-board records sit beside the board log's"
    " reads and appends; B1 removes it",
    # intents.py:33, for Board and Clock.
    ("pptmstr.boardlog", "pptmstr.intents"): "BoardRestored carries a Board; B1 removes it",
    # boardlog.py:131, for read_regular.
    ("pptmstr.ondisk", "pptmstr.boardlog"): "the board log reads its files through ondisk;"
    " B1 removes it",
}


def test_the_disk_scan_sees_a_reader_and_a_pure_module() -> None:
    readers = disk_readers()
    assert "pptmstr.sessionfile" in readers
    assert "pptmstr.model" not in readers
    # ondisk's only read is ``os.open``; boardlog reaches it only by a relative
    # ``from .ondisk import read_regular``, which the import scan resolves.
    assert "pptmstr.ondisk" in readers
    assert any(
        edge.source == "pptmstr.boardlog" and edge.target == "pptmstr.ondisk" for edge in imports()
    )


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
            edge("pptmstr.store.arms", "pptmstr.s", "typing"),
        ),
        readers=frozenset({"pptmstr.r", "pptmstr.s"}),
        known=known,
    )
    assert set(found) == {
        ("pptmstr.r", "pptmstr.a"),
        ("pptmstr.s", "pptmstr.r"),
        ("pptmstr.s", STORE),
    }
    assert found[("pptmstr.r", "pptmstr.a")][0] == "pptmstr.store.arms"


def test_store_reaches_no_module_that_reads_the_disk() -> None:
    readers = disk_readers()
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
    # boardgraph.py:5
    "pptmstr.boardgraph": Claim(
        "pptmstr/boardgraph.py", "Every function here is pure over one ``Snapshot``"
    ),
    # digest.py:8-9
    "pptmstr.digest": Claim(
        "pptmstr/digest.py", "``digest`` is a pure function of a snapshot, a session and a point"
    ),
    # commandtext.py:4
    "pptmstr.commandtext": NO_IMGUI["pptmstr.commandtext"],
    # shellscan.py:4
    "pptmstr.shellscan": NO_IMGUI["pptmstr.shellscan"],
    # sandbox.py:6
    "pptmstr.sandbox": Claim("pptmstr/sandbox.py", "no IO, no clock, no launch"),
    # replies.py:11
    "pptmstr.replies": Claim("pptmstr/replies.py", "Pure: no SDK, no clock, no IO"),
    # naming.py:5
    "pptmstr.naming": Claim(
        "pptmstr/naming.py", "Pure: no clock, no IO, and no import from pptmstr"
    ),
    # jsonfields.py:4
    "pptmstr.jsonfields": Claim("pptmstr/jsonfields.py", "Pure: no IO, no clock, no state"),
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
}

# store holds the reducer and the Store cell around it. STYLE.md:49-50: ``now`` is a
# parameter so the core cannot read a clock; the entry point supplies it, and this is
# the one function that does.
STORE_CLOCK_SITES = frozenset({"_clock"})


@pytest.mark.parametrize("module", sorted(PURE))
def test_a_module_called_pure_is_still_called_pure(module: str) -> None:
    claim = PURE[module]
    assert module in modules(), f"{module} is gone: delete its PURE entry"
    assert (
        claim.holds()
    ), f"{claim.file} no longer says {claim.words!r}: delete the PURE entry for {module}"


@pytest.mark.parametrize("module", sorted(PURE))
def test_a_pure_module_reads_no_clock(module: str) -> None:
    found = [call for call in calls(tree_of(module)) if call.dotted in _CLOCKS]
    assert not found, f"{module} is pure ({PURE[module].file}) and reads a clock: " + ", ".join(
        f"{call.dotted} in {call.function} (line {call.line})" for call in found
    )


def test_store_reads_a_clock_only_at_its_entry_point() -> None:
    sites = {call.function for call in calls(tree_of("pptmstr.store")) if call.dotted in _CLOCKS}
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


def test_naming_imports_nothing_from_pptmstr() -> None:
    """
    What lets the driver and the board log name agents without importing
    ``board``. ``model`` reaches ``board`` through ``approval``'s lazy imports,
    so one edge into the package of any kind is the edge back.
    """
    found = [edge for edge in imports_of("pptmstr.naming") if edge.target.startswith("pptmstr")]
    assert not found, ", ".join(f"{edge.target} ({edge.kind}, line {edge.line})" for edge in found)


def test_nothing_reaches_a_moved_name_through_board() -> None:
    """
    No shim: a name ``naming`` holds is imported from it, never from ``board``.
    """
    moved = {"LEAD", "ROOT_NAMES", "address_for", "reserved"}
    found = [
        f"{module} line {node.lineno}"
        for module in modules()
        if module != "pptmstr.board"
        for node in ast.walk(tree_of(module))
        if isinstance(node, ast.ImportFrom)
        and (node.module == "pptmstr.board" or (node.level and node.module == "board"))
        and any(alias.name in moved for alias in node.names)
    ]
    assert not found, "imports a naming name through board: " + ", ".join(found)
