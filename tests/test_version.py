"""
``pptmstr --version``, the one source of pptmstr's version, and where a process
finds the text library (``pptmstr.pairing``, ``pptmstr.version``,
``pptmstr.__main__``).

The report is for installs where something is wrong, so most of what is held
here is that each wrong thing is a sentence and the exit status is 0: no
library, a library that is refused, an ``imgui-bundle`` that is not the pinned
one, no advtext at all. Runs of the whole report are in an interpreter of their
own, because the report's first act is to settle the library's two variables in
its own environment, which must not become this process's.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
from advtext import typesetter

from pptmstr import __main__ as entry
from pptmstr import app, pairing, version
from pptmstr.ui.typeset import gate

ROOT = Path(__file__).resolve().parent.parent
REF = "a" * 40
OTHER = "b" * 40
VARIABLES = (pairing.LIBRARY_VARIABLE, pairing.PACKAGE_VARIABLE)


def _pyproject() -> dict[str, Any]:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _run(*args: str, script: str | None = None, **env: str) -> subprocess.CompletedProcess[str]:
    """
    ``python -m pptmstr ARGS``, or ``script``, with the library's variables as given and no others.
    """
    clean = {k: v for k, v in os.environ.items() if not k.startswith("IMTYPESET_")}
    command = ["-c", script] if script is not None else ["-m", "pptmstr", *args]
    return subprocess.run(
        [sys.executable, *command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={**clean, **env},
    )


def _lines(done: subprocess.CompletedProcess[str]) -> dict[str, str]:
    """
    The report by the word each line starts with, having held it to an exit of 0 and no traceback.
    """
    assert done.returncode == 0, done.stderr
    assert "Traceback" not in done.stderr and "Traceback" not in done.stdout
    lines = done.stdout.splitlines()
    assert len(lines) == 6, done.stdout
    return {re.split(r"[ :]", line, maxsplit=1)[0]: line for line in lines}


# -- one source for each thing that is named twice ---------------------------------


def test_the_version_is_written_once_and_the_build_reads_it_from_the_package() -> None:
    project = _pyproject()
    assert "version" not in project["project"]
    assert project["project"]["dynamic"] == ["version"]
    assert project["tool"]["hatch"]["version"]["path"] == "pptmstr/__init__.py"
    import pptmstr

    assert re.fullmatch(r"\d+\.\d+\.\d+", pptmstr.__version__)
    assert version._pptmstr().startswith(f"pptmstr {pptmstr.__version__} ")


def test_the_pin_the_report_names_is_the_one_the_build_declares() -> None:
    (spec,) = [d for d in _pyproject()["project"]["dependencies"] if d.startswith("imgui-bundle")]
    assert spec == f"imgui-bundle=={pairing.IMGUI_BUNDLE_PIN}"


def test_the_variables_pptmstr_sets_are_the_ones_advtext_reads() -> None:
    assert VARIABLES == (typesetter.LIBRARY_VARIABLE, typesetter.PACKAGE_VARIABLE)


def test_the_ref_files_go_into_the_wheel_inside_the_package_and_into_the_sdist() -> None:
    hatch = _pyproject()["tool"]["hatch"]["build"]["targets"]
    assert hatch["wheel"]["force-include"] == {
        pairing.ADVTEXT_REF: f"pptmstr/{pairing.ADVTEXT_REF}",
        pairing.IMTYPESET_REF: f"pptmstr/{pairing.IMTYPESET_REF}",
    }
    assert {f"/{pairing.ADVTEXT_REF}", f"/{pairing.IMTYPESET_REF}"} <= set(
        hatch["sdist"]["include"]
    )


def test_the_rule_imports_where_nothing_but_the_standard_library_does() -> None:
    # `scripts/typeset_deps.py` imports it in a .venv that holds nothing yet.
    # -S: no site-packages, so anything third-party would fail the import.
    done = subprocess.run(
        [sys.executable, "-S", "-c", "import pptmstr.pairing; print(pptmstr.pairing.__file__)"],
        capture_output=True,
        text=True,
        check=False,
        env={"PYTHONPATH": str(ROOT)},
    )
    assert done.stdout.strip() == str(ROOT / "pptmstr" / "pairing.py"), done.stderr


# -- where a process finds the library ----------------------------------------------


def _checkout(top: Path, *, commit: str = REF, prefix: Path | None = None) -> Path:
    """
    A checkout's root with a pair in its ``.deps`` as ``make deps`` leaves one.
    """
    pair = top / pairing.DEPS_DIR / pairing.PAIR_DIR
    (pair / pairing.PACKAGE_DIR / "imtypeset").mkdir(parents=True)
    (pair / pairing.PACKAGE_DIR / "imtypeset" / "__init__.py").write_text("", encoding="utf-8")
    (pair / pairing.LIBRARY_NAME).write_bytes(b"")
    built_for = f"for: {prefix / 'bin' / 'python'}\n" if prefix else ""
    (pair / pairing.BUILT_FROM).write_text(
        f"imtypeset: somewhere\ncommit: {commit}\n{built_for}", encoding="utf-8"
    )
    (top / pairing.IMTYPESET_REF).write_text(f"# a comment\n{REF}\n", encoding="utf-8")
    (top / "pyproject.toml").write_text("", encoding="utf-8")
    return pair


def test_a_checkout_with_a_whole_pair_at_the_ref_sets_both_variables(tmp_path: Path) -> None:
    pair = _checkout(tmp_path, prefix=tmp_path / "venv")
    environ: dict[str, str] = {"PATH": "/bin"}

    found = pairing.decide(environ, tmp_path, tmp_path / "venv")

    assert found.route == pairing.ROUTE_CHECKOUT
    assert environ == {
        "PATH": "/bin",
        pairing.LIBRARY_VARIABLE: str(pair / pairing.LIBRARY_NAME),
        pairing.PACKAGE_VARIABLE: str(pair / pairing.PACKAGE_DIR),
    }


@pytest.mark.parametrize("mine", VARIABLES)
@pytest.mark.parametrize("value", ["/another/project", ""])
def test_a_variable_the_caller_set_is_never_paired_with_this_checkouts(
    tmp_path: Path, mine: str, value: str
) -> None:
    # Empty too: `IMTYPESET_LIBRARY=` is how the caller says "without the library".
    _checkout(tmp_path)
    environ = {mine: value}

    found = pairing.decide(environ, tmp_path, tmp_path / "venv")

    assert found.route == pairing.ROUTE_ENVIRONMENT and mine in found.why
    assert environ == {mine: value}


def test_an_installed_pptmstr_with_no_checkout_looks_nowhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A wheel: the package in a site-packages, the ref files inside it, and a
    # `.deps` beside it that is somebody else's and must not be read.
    site = tmp_path / "site-packages"
    _checkout(site)
    (site / "pyproject.toml").unlink()
    (site / "pptmstr").mkdir()
    (site / pairing.IMTYPESET_REF).rename(site / "pptmstr" / pairing.IMTYPESET_REF)
    monkeypatch.setattr(pairing, "PACKAGE", site / "pptmstr")
    assert pairing.checkout() is None
    assert pairing.ref_path(pairing.IMTYPESET_REF) == site / "pptmstr" / pairing.IMTYPESET_REF

    environ: dict[str, str] = {}
    found = pairing.decide(environ, pairing.checkout())

    assert found.route == pairing.ROUTE_NONE and "no source checkout" in found.why
    assert environ == {}


def test_this_tree_is_a_checkout_and_its_ref_files_are_at_its_root() -> None:
    assert pairing.checkout() == ROOT
    assert pairing.ref_path(pairing.ADVTEXT_REF) == ROOT / pairing.ADVTEXT_REF


def _spoil_nothing(pair: Path) -> None:
    pass


def _spoil_the_library(pair: Path) -> None:
    (pair / pairing.LIBRARY_NAME).unlink()


def _spoil_the_package(pair: Path) -> None:
    (pair / pairing.PACKAGE_DIR / "imtypeset" / "__init__.py").unlink()


def _spoil_the_record(pair: Path) -> None:
    (pair / pairing.BUILT_FROM).write_bytes(b"\xff\n")


def _spoil_the_ref(pair: Path) -> None:
    (pair.parent.parent / pairing.IMTYPESET_REF).write_text("main\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("commit", "spoil", "says"),
    [
        (OTHER, _spoil_nothing, OTHER),
        (REF, _spoil_the_library, pairing.LIBRARY_NAME),
        (REF, _spoil_the_package, "not a whole pair"),
        (REF, _spoil_the_record, "UnicodeDecodeError"),
        (REF, _spoil_the_ref, "not a full 40-digit commit hash"),
    ],
)
def test_a_pair_that_is_not_the_pinned_one_whole_is_left_alone_and_the_reason_said(
    tmp_path: Path, commit: str, spoil: Any, says: str
) -> None:
    pair = _checkout(tmp_path, commit=commit)
    spoil(pair)
    environ: dict[str, str] = {}

    found = pairing.decide(environ, tmp_path, tmp_path / "venv")

    assert found.route == pairing.ROUTE_NONE and says in found.why
    assert "\n" not in found.why
    assert environ == {}


def test_a_checkout_with_nothing_built_says_what_builds_it(tmp_path: Path) -> None:
    environ: dict[str, str] = {}
    found = pairing.decide(environ, tmp_path, tmp_path / "venv")
    assert found.route == pairing.ROUTE_NONE and "make deps" in found.why
    assert environ == {}


def test_a_pair_built_for_another_environment_is_not_this_ones(tmp_path: Path) -> None:
    # Linked against that environment's wheel binary: taken, it would be
    # "refused", a fault, where the truth is that this one has no library.
    _checkout(tmp_path, prefix=tmp_path / "theirs")
    environ: dict[str, str] = {}

    found = pairing.decide(environ, tmp_path, tmp_path / "mine")

    assert found.route == pairing.ROUTE_NONE
    assert str(tmp_path / "theirs") in found.why and str(tmp_path / "mine") in found.why
    assert environ == {}


@pytest.mark.skipif(os.geteuid() == 0, reason="root searches any directory")
def test_a_deps_directory_that_cannot_be_searched_is_a_reason_and_not_a_traceback(
    tmp_path: Path,
) -> None:
    # Every start of pptmstr decides this first, --version included.
    _checkout(tmp_path)
    deps = tmp_path / pairing.DEPS_DIR
    deps.chmod(0o000)
    try:
        environ: dict[str, str] = {}
        found = pairing.decide(environ, tmp_path)
    finally:
        deps.chmod(0o755)
    assert found.route == pairing.ROUTE_NONE and environ == {}
    assert "could not be checked (PermissionError" in found.why


def test_the_process_decides_once_and_does_not_mistake_its_own_answer_for_the_callers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pair = _checkout(tmp_path, prefix=Path(sys.prefix))
    for name in VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(pairing, "_FOUND", None)
    monkeypatch.setattr(pairing, "checkout", lambda: tmp_path)
    try:
        first = pairing.adopt()
        assert first.route == pairing.ROUTE_CHECKOUT
        assert os.environ[pairing.LIBRARY_VARIABLE] == str(pair / pairing.LIBRARY_NAME)
        assert pairing.adopt() is first
    finally:
        # monkeypatch restores what it deleted and knows nothing of what `adopt` set.
        for name in VARIABLES:
            os.environ.pop(name, None)


# -- what a process pptmstr launches is given ------------------------------------------


def _built_pair() -> Path | None:
    """
    This checkout's pair when a process started here would take it, else ``None``.
    """
    environ: dict[str, str] = {}
    if pairing.decide(environ, ROOT).route != pairing.ROUTE_CHECKOUT:
        return None
    return Path(environ[pairing.LIBRARY_VARIABLE]).parent


def _adopted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, environ: dict[str, str]) -> Path:
    """
    ``pairing`` as it is once ``adopt`` has decided for a checkout in ``environ``; the pair.
    """
    pair = _checkout(tmp_path, prefix=Path(sys.prefix))
    monkeypatch.setattr(pairing, "_FOUND", pairing.decide(environ, tmp_path))
    return pair


def test_what_a_checkout_set_for_itself_is_taken_back_and_still_answered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in VARIABLES:
        monkeypatch.delenv(name, raising=False)
    environ = {"PATH": "/bin"}
    pair = _adopted(tmp_path, monkeypatch, environ)
    assert set(VARIABLES) < set(environ)

    pairing.release(environ)

    assert environ == {"PATH": "/bin"}
    assert pairing.named(pairing.LIBRARY_VARIABLE) == str(pair / pairing.LIBRARY_NAME)
    assert pairing.named(pairing.PACKAGE_VARIABLE) == str(pair / pairing.PACKAGE_DIR)
    pairing.release(environ)
    assert environ == {"PATH": "/bin"}


def test_a_pair_the_caller_set_is_passed_on_as_it_came(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _checkout(tmp_path)
    environ = {pairing.LIBRARY_VARIABLE: "/another/lib.so", pairing.PACKAGE_VARIABLE: ""}
    monkeypatch.setattr(pairing, "_FOUND", pairing.decide(dict(environ), tmp_path))
    assert pairing._FOUND is not None and pairing._FOUND.route == pairing.ROUTE_ENVIRONMENT

    kept = dict(environ)
    pairing.release(kept)

    assert kept == environ
    monkeypatch.setenv(pairing.LIBRARY_VARIABLE, "/another/lib.so")
    assert pairing.named(pairing.LIBRARY_VARIABLE) == "/another/lib.so"


def test_a_variable_changed_since_is_somebody_elses_and_is_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environ: dict[str, str] = {}
    pair = _adopted(tmp_path, monkeypatch, environ)
    environ[pairing.LIBRARY_VARIABLE] = "/set/later.so"

    pairing.release(environ)

    assert environ == {pairing.LIBRARY_VARIABLE: "/set/later.so"}
    assert pair.is_dir()


def test_nothing_is_taken_back_before_the_process_has_decided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pairing, "_FOUND", None)
    environ = {pairing.LIBRARY_VARIABLE: "/x.so"}
    pairing.release(environ)
    assert environ == {pairing.LIBRARY_VARIABLE: "/x.so"}
    monkeypatch.delenv(pairing.LIBRARY_VARIABLE, raising=False)
    assert pairing.named(pairing.LIBRARY_VARIABLE) is None


def test_the_library_is_asked_before_its_variables_are_taken_back_whatever_asking_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    monkeypatch.setattr(typesetter, "module", lambda: order.append("asked"))
    monkeypatch.setattr(pairing, "release", lambda: order.append("released"))
    gate.settle()
    assert order == ["asked", "released"]

    def gone() -> Any:
        raise ModuleNotFoundError("No module named 'advtext'")

    monkeypatch.setattr(gate, "_typesetter", gone)
    gate.settle()
    assert order == ["asked", "released", "released"]


_CHILD = (
    "import json, os, subprocess, sys\n"
    "from pptmstr import __main__ as entry, app, pairing\n"
    "def application(argv):\n"
    "    # What the SDK's transport starts a session's process with: os.environ.\n"
    "    child = subprocess.run(\n"
    "        [sys.executable, '-c', 'import json, os; print(json.dumps('\n"
    "         '{k: v for k, v in os.environ.items() if k.startswith(\"IMTYPESET_\")}))'],\n"
    "        capture_output=True, text=True, check=True)\n"
    "    from imgui_bundle import imgui\n"
    "    from pptmstr import theme\n"
    "    from pptmstr.ui import typeset\n"
    "    context = imgui.create_context()\n"
    "    io = imgui.get_io()\n"
    "    io.display_size = imgui.ImVec2(800.0, 600.0)\n"
    "    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures.value\n"
    "    io.fonts.add_font_default()\n"
    "    bold = theme._ASSETS / 'fonts' / 'Inconsolata-Bold.ttf'\n"
    "    loaded = [typeset.load_fonts(bold)]\n"
    "    cache = typeset.ViewCache()\n"
    "    for _ in range(2):\n"
    "        typeset.pre_frame(); imgui.new_frame(); imgui.begin('w')\n"
    "        typeset.markdown(cache, 'k', 'Some *text* to set.')\n"
    "        imgui.end(); imgui.render()\n"
    "    loaded.append(typeset.load_fonts(bold))\n"
    "    print(json.dumps({'child': json.loads(child.stdout), 'loaded': loaded,\n"
    "        'unset': cache.unbuilt('k'), 'route': pairing.adopt().route,\n"
    "        'named': pairing.named(pairing.LIBRARY_VARIABLE),\n"
    "        'reason': typeset.unavailable_reason()}))\n"
    "    typeset.unload(); imgui.destroy_context(context)\n"
    "    return 0\n"
    "app.main = application\n"
    "raise SystemExit(entry.main(['--fake']))\n"
)


@pytest.mark.skipif(_built_pair() is None, reason="no pair built for this checkout's interpreter")
@pytest.mark.skipif(
    os.environ.get(pairing.LIBRARY_VARIABLE) == "", reason="this run is the one without the library"
)
def test_a_process_pptmstr_starts_is_not_given_the_pair_pptmstr_found_for_itself() -> None:
    """
    Started with neither variable, a checkout sets both for itself; a session's
    process started from it would read them as set by its caller, and another
    checkout's tests would run on this one's library. By the time the
    application runs a child is given neither, and the library that was found
    through them still loads its fonts, draws, and loads them again.
    """
    pair = _built_pair()
    assert pair is not None
    done = _run(script=_CHILD)
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout.splitlines()[-1])
    assert seen["route"] == pairing.ROUTE_CHECKOUT
    assert seen["child"] == {}
    assert seen["loaded"] == [True, True] and seen["unset"] == 0, seen["reason"]
    assert seen["named"] == str(pair / pairing.LIBRARY_NAME)


_AGAIN = (
    "import json, os, subprocess, sys\n"
    "from pptmstr import __main__ as entry, app, pairing\n"
    "def named():\n"
    "    return sorted(k for k in os.environ if k.startswith('IMTYPESET_'))\n"
    "def application(argv):\n"
    "    from advtext import typesetter\n"
    "    seen = {'route': pairing.adopt().route, 'released': named(),\n"
    "            'first': typesetter.kind().value}\n"
    "    typesetter.reset()\n"
    "    seen['again'] = typesetter.module() is not None\n"
    "    seen['kind'] = typesetter.kind().value\n"
    "    seen['reason'] = typesetter.reason()\n"
    "    seen['named_by'] = typesetter.pairing()['named_by']\n"
    "    seen['library'] = typesetter.pairing()['library']\n"
    "    seen['after'] = named()\n"
    "    child = subprocess.run(\n"
    "        [sys.executable, '-c', 'import os; print(sorted('\n"
    "         'k for k in os.environ if k.startswith(\"IMTYPESET_\")))'],\n"
    "        capture_output=True, text=True, check=True)\n"
    "    seen['child'] = child.stdout.strip()\n"
    "    print(json.dumps(seen))\n"
    "    return 0\n"
    "app.main = application\n"
    "raise SystemExit(entry.main(['--fake']))\n"
)


@pytest.mark.skipif(_built_pair() is None, reason="no pair built for this checkout's interpreter")
@pytest.mark.skipif(
    os.environ.get(pairing.LIBRARY_VARIABLE) == "", reason="this run is the one without the library"
)
def test_the_library_asked_again_is_found_again_after_its_variables_were_taken_back() -> None:
    """
    The library is asked again after a ``typesetter.reset()``: a test process
    that provoked a failure, an operator's "try again". By then the entry
    point has taken both variables back out of the environment, and a library
    that looked only there would answer "not installed" beside the pair it had
    just been drawing with. It remembers the pair a load by the environment was
    accepted with, so the second asking loads it; nothing is put back in the
    environment for that, and a child is still given neither variable.
    """
    pair = _built_pair()
    assert pair is not None
    done = _run(script=_AGAIN)
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout.splitlines()[-1])
    assert seen["route"] == pairing.ROUTE_CHECKOUT
    assert seen["released"] == [] and seen["first"] == gate.READY
    assert seen["again"] is True and seen["kind"] == gate.READY, seen["reason"]
    assert seen["named_by"] == typesetter.NAMED_BY_MEMORY
    assert seen["library"] == str(pair / pairing.LIBRARY_NAME)
    assert seen["after"] == [] and seen["child"] == "[]"


@pytest.mark.skipif(_built_pair() is None, reason="no pair built for this checkout's interpreter")
@pytest.mark.skipif(
    os.environ.get(pairing.LIBRARY_VARIABLE) == "", reason="this run is the one without the library"
)
def test_a_process_pptmstr_starts_is_given_the_pair_pptmstrs_caller_named() -> None:
    pair = _built_pair()
    assert pair is not None
    named = {
        pairing.LIBRARY_VARIABLE: str(pair / pairing.LIBRARY_NAME),
        pairing.PACKAGE_VARIABLE: str(pair / pairing.PACKAGE_DIR),
    }
    done = _run(script=_CHILD, **named)
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout.splitlines()[-1])
    assert seen["route"] == pairing.ROUTE_ENVIRONMENT
    assert seen["child"] == named
    assert seen["loaded"] == [True, True]


@pytest.mark.skipif(_built_pair() is None, reason="no pair built for this checkout's interpreter")
def test_the_report_asked_for_through_the_application_still_names_the_checkouts_pair() -> None:
    # `--fake --version` is the application's parser's, after the variables were taken back.
    lines = _lines(_run("--fake", "--version"))
    assert "found through this checkout's .deps" in lines["imtypeset"]
    assert lines["typeset"].startswith("typeset text: the library is accepted beside this wheel")


# -- the entry point ----------------------------------------------------------------


def test_the_command_and_the_module_are_one_entry_that_decides_before_the_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _pyproject()["project"]["scripts"] == {"pptmstr": "pptmstr.__main__:main"}
    order: list[Any] = []
    monkeypatch.setattr(pairing, "adopt", lambda: order.append("adopt"))
    monkeypatch.setattr(gate, "settle", lambda: order.append("settle"))
    monkeypatch.setattr(app, "main", lambda argv: order.append(argv) or 7)

    assert entry.main(["--fake"]) == 7

    # The library is asked for, and the variables taken back, before the
    # application can start a session that would inherit them.
    assert order == ["adopt", "settle", ["--fake"]]


def test_the_applications_own_parser_answers_version_the_same_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Whoever calls `app.main` directly, or abbreviates the flag past the entry.
    monkeypatch.setattr(version, "main", lambda: 0)
    monkeypatch.setattr(app.settings_mod, "load", lambda: pytest.fail("the application started"))
    assert app.main(["--version"]) == 0
    assert app.main(["--vers"]) == 0


def test_version_is_answered_without_the_application_or_an_imgui_context() -> None:
    done = _run(
        script=(
            "import sys\n"
            "from pptmstr.__main__ import main\n"
            "status = main(['--version'])\n"
            "from imgui_bundle import imgui\n"
            "assert 'pptmstr.app' not in sys.modules, 'the application was imported'\n"
            "assert 'OpenGL' not in sys.modules\n"
            "assert imgui.get_current_context() is None\n"
            "raise SystemExit(status)\n"
        ),
        IMTYPESET_LIBRARY="",
    )
    assert _lines(done)["pptmstr"].startswith("pptmstr ")


@pytest.mark.parametrize(
    "args",
    [["--task", "--version"], ["--theme", "--version"], ["--", "--version"]],
)
def test_version_where_another_options_value_goes_is_the_applications_parser_to_read(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    """
    Only the application's parser knows which options take a value, and it
    refuses each of these. The entry answers ``--version`` itself where nothing
    before it could be an option waiting for one, and hands the rest over.
    """
    order: list[Any] = []
    monkeypatch.setattr(pairing, "adopt", lambda: None)
    monkeypatch.setattr(gate, "settle", lambda: None)
    monkeypatch.setattr(version, "main", lambda: order.append("report") or 0)
    monkeypatch.setattr(app, "main", lambda argv: order.append(argv) or 2)
    assert entry.main(args) == 2
    assert order == [args]
    with pytest.raises(SystemExit) as refused:
        monkeypatch.undo()
        monkeypatch.setattr(version, "main", lambda: pytest.fail("the report was printed"))
        app.main(args)
    assert refused.value.code == 2


@pytest.mark.parametrize(
    "args", [["--version"], ["--theme=dark", "--version"], ["--version", "--fake"]]
)
def test_version_where_a_flag_goes_is_answered_before_the_application(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    monkeypatch.setattr(pairing, "adopt", lambda: None)
    monkeypatch.setattr(version, "main", lambda: 0)
    monkeypatch.setattr(app, "main", lambda argv: pytest.fail("the application was asked"))
    assert entry.main(args) == 0


def test_the_report_is_printed_to_an_output_that_cannot_encode_it() -> None:
    """
    A path with a character the terminal's encoding lacks, under a locale that
    is not UTF-8: the character is written as its escape and the rest whole.
    """
    done = _run(
        "--version",
        IMTYPESET_LIBRARY="/nowhere/caf\u00e9/lib.so",
        PYTHONIOENCODING="ascii",
    )
    lines = _lines(done)
    assert "/nowhere/caf\\xe9/lib.so" in lines["imtypeset"]


def test_the_report_into_a_pipe_nobody_reads_is_not_a_traceback() -> None:
    # `pptmstr --version | head -1`, with the reader gone before the first line.
    clean = {k: v for k, v in os.environ.items() if not k.startswith("IMTYPESET_")}
    reading, writing = os.pipe()
    os.close(reading)
    try:
        done = subprocess.run(
            [sys.executable, "-m", "pptmstr", "--version"],
            cwd=ROOT,
            stdout=writing,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            check=False,
            env={**clean, "IMTYPESET_LIBRARY": ""},
        )
    finally:
        os.close(writing)
    assert done.stderr == "" and done.returncode == 0


# -- the report, each wrong thing a sentence ------------------------------------------


def test_with_no_library_the_report_says_not_installed_and_exits_0() -> None:
    lines = _lines(_run("--version", IMTYPESET_LIBRARY=""))

    assert lines["typeset"] == (
        "typeset text: not in use (not-installed): "
        "the typesetting library is not installed (IMTYPESET_LIBRARY is not set)"
    )
    assert lines["imtypeset"].startswith("imtypeset: no library (IMTYPESET_LIBRARY set by")
    assert pairing.read_ref(ROOT / pairing.IMTYPESET_REF) in lines["imtypeset"]
    assert lines["python"].endswith(f"({sys.executable})")


def test_a_library_that_is_named_and_is_not_there_is_refused_in_a_sentence(
    tmp_path: Path,
) -> None:
    lines = _lines(
        _run(
            "--version",
            IMTYPESET_LIBRARY=str(tmp_path / pairing.LIBRARY_NAME),
            IMTYPESET_PYTHON=str(tmp_path / pairing.PACKAGE_DIR),
        )
    )

    assert lines["typeset"].startswith(
        "typeset text: not in use (refused): the typesetting library was refused ("
    )
    assert lines["imtypeset"].startswith("imtypeset at an unrecorded commit: ")
    assert "found through the environment" in lines["imtypeset"]


def test_an_imgui_bundle_that_is_not_the_pinned_one_is_a_sentence_naming_both() -> None:
    done = _run(
        script=(
            "import imgui_bundle\n"
            "from pptmstr import version\n"
            "imgui_bundle.__version__ = '1.0.1'\n"
            "raise SystemExit(version.main())\n"
        ),
        IMTYPESET_LIBRARY="",
    )
    assert _lines(done)["imgui-bundle"] == (
        f"imgui-bundle 1.0.1 is running and pptmstr pins {pairing.IMGUI_BUNDLE_PIN}: the "
        f"typesetting library is built for one wheel and is refused beside another"
    )


def test_the_pinned_imgui_bundle_is_said_to_be_the_pinned_one() -> None:
    import imgui_bundle

    assert version._imgui_bundle() == (
        f"imgui-bundle {imgui_bundle.__version__}, the version pptmstr pins"
    )


def test_with_advtext_unimportable_the_report_says_so_twice_and_exits_0() -> None:
    done = _run(
        script=(
            "import sys\n"
            "sys.modules['advtext'] = None\n"
            "from pptmstr import version\n"
            "raise SystemExit(version.main())\n"
        ),
        IMTYPESET_LIBRARY="",
    )
    lines = _lines(done)
    assert lines["advtext"].startswith("advtext: not installed (")
    assert pairing.read_ref(ROOT / pairing.ADVTEXT_REF) in lines["advtext"]
    assert lines["typeset"].startswith(
        "typeset text: not in use (not-installed): the text library is not installed ("
    )


def test_a_part_that_raises_costs_its_own_line_and_no_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken() -> str:
        raise RuntimeError("no such thing")

    monkeypatch.setattr(version, "_python", broken)
    monkeypatch.setattr(version, "_typeset", lambda: "typeset text: not asked")

    lines = version.report(pairing.Found(pairing.ROUTE_NONE, "for the test")).splitlines()

    assert lines[1] == "python: could not be read (RuntimeError: no such thing)"
    assert len(lines) == 6 and lines[0].startswith("pptmstr ") and lines[5].endswith("not asked")


@pytest.mark.skipif(_built_pair() is None, reason="no pair built for this checkout's interpreter")
def test_started_with_neither_variable_a_checkout_finds_its_own_pair_and_it_is_accepted() -> None:
    lines = _lines(_run("--version"))

    asked = pairing.read_ref(ROOT / pairing.IMTYPESET_REF)
    assert lines["imtypeset"].startswith(f"imtypeset {asked}: ")
    assert "found through this checkout's .deps" in lines["imtypeset"]
    assert lines["imtypeset"].endswith(f"the commit {pairing.IMTYPESET_REF} names")
    assert lines["typeset"].startswith("typeset text: the library is accepted beside this wheel")


@pytest.mark.skipif(_built_pair() is None, reason="no pair built for this checkout's interpreter")
def test_the_same_pair_named_by_the_caller_is_said_to_come_from_the_environment() -> None:
    pair = _built_pair()
    assert pair is not None
    lines = _lines(
        _run(
            "--version",
            IMTYPESET_LIBRARY=str(pair / pairing.LIBRARY_NAME),
            IMTYPESET_PYTHON=str(pair / pairing.PACKAGE_DIR),
        )
    )
    assert "found through the environment (IMTYPESET_LIBRARY and IMTYPESET_PYTHON set by" in (
        lines["imtypeset"]
    )
    assert lines["typeset"].startswith("typeset text: the library is accepted beside this wheel")


# -- what is installed, against what the tree asks for --------------------------------


def _installed_advtext(
    top: Path, monkeypatch: pytest.MonkeyPatch, direct_url: dict[str, Any]
) -> Path:
    """
    An ``advtext`` package in a site directory of its own, with a distribution that
    records ``direct_url``; the package's directory.
    """
    site = top / "site"
    package = site / "advtext"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("NAME = 'advtext'\n", encoding="utf-8")
    record = site / "advtext-0.1.0.dist-info"
    record.mkdir()
    (record / "METADATA").write_text("Name: advtext\nVersion: 0.1.0\n", encoding="utf-8")
    (record / "direct_url.json").write_text(json.dumps(direct_url), encoding="utf-8")
    monkeypatch.setattr(
        importlib.metadata, "distribution", lambda name: importlib.metadata.Distribution.at(record)
    )
    return package


def _clone(top: Path, head: str) -> Path:
    clone = top / "clone"
    (clone / ".git").mkdir(parents=True)
    (clone / ".git" / "HEAD").write_text(f"{head}\n", encoding="utf-8")
    (clone / "advtext").mkdir()
    (clone / "advtext" / "__init__.py").write_text("NAME = 'advtext'\n", encoding="utf-8")
    return clone


def test_a_copy_installed_from_a_checkout_is_that_checkouts_commit_only_file_for_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = _clone(tmp_path, REF)
    package = _installed_advtext(tmp_path, monkeypatch, {"url": clone.as_uri(), "dir_info": {}})

    assert version._installed_from(package) == (REF, f"file for file the checkout at {clone}")

    # The clone moved on after the install: `make deps` fetched and did not install.
    (clone / "advtext" / "__init__.py").write_text("NAME = 'newer'\n", encoding="utf-8")
    commit, how = version._installed_from(package)
    assert commit is None and "1 file(s) differ" in how and "__init__.py" in how


def test_a_checkout_on_a_branch_names_no_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = _clone(tmp_path, "ref: refs/heads/main")
    package = _installed_advtext(tmp_path, monkeypatch, {"url": clone.as_uri(), "dir_info": {}})
    commit, how = version._installed_from(package)
    assert commit is None and "on a branch" in how


def test_a_copy_installed_from_version_control_is_the_commit_its_record_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://example.invalid/advtext.git"
    package = _installed_advtext(
        tmp_path, monkeypatch, {"url": url, "vcs_info": {"vcs": "git", "commit_id": OTHER}}
    )
    assert version._installed_from(package) == (OTHER, f"installed from {url}")


def test_a_distribution_that_is_another_copy_says_nothing_of_the_one_imported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = _clone(tmp_path, REF)
    _installed_advtext(tmp_path, monkeypatch, {"url": clone.as_uri(), "dir_info": {}})
    commit, how = version._installed_from(tmp_path / "elsewhere" / "advtext")
    assert commit is None and "another copy" in how


def test_the_advtext_line_says_plainly_when_what_runs_is_not_what_the_tree_asks_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = Path(typesetter.__file__).resolve().parent
    monkeypatch.setattr(version, "_asked_for", lambda name: REF)

    monkeypatch.setattr(version, "_installed_from", lambda found: (REF, "as recorded"))
    assert version._advtext() == (
        f"advtext {REF}: imported from {package}, as recorded; the commit advtext.ref names"
    )

    monkeypatch.setattr(version, "_installed_from", lambda found: (OTHER, "as recorded"))
    line = version._advtext()
    assert (
        line.startswith(f"advtext {OTHER}: ")
        and f"NOT the commit advtext.ref names, which is {REF}" in line
    )

    monkeypatch.setattr(version, "_installed_from", lambda found: (None, "nothing records it"))
    line = version._advtext()
    assert line.startswith("advtext at an unrecorded commit: ") and "nothing records it" in line
    assert "nothing here says whether this is it" in line


def test_the_library_line_is_held_to_built_from_and_not_to_the_ref_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pair = _checkout(tmp_path, commit=OTHER)
    monkeypatch.setenv(pairing.LIBRARY_VARIABLE, str(pair / pairing.LIBRARY_NAME))
    monkeypatch.setattr(version, "_asked_for", lambda name: REF)
    found = pairing.Found(pairing.ROUTE_ENVIRONMENT, "set by the caller")

    line = version._imtypeset(found)
    assert line.startswith(f"imtypeset {OTHER}: {pair / pairing.LIBRARY_NAME}, ")
    assert f"NOT the commit imtypeset.ref names, which is {REF}" in line

    (pair / pairing.BUILT_FROM).unlink()
    line = version._imtypeset(found)
    assert line.startswith("imtypeset at an unrecorded commit: ") and REF in line


# -- the kind, asked of the library where it can say ---------------------------------


def test_the_librarys_own_kind_is_the_answer_whatever_the_variables_say(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A library found with neither variable set, one named and refused once
    # its variable was taken back, and a platform the library does not serve.
    monkeypatch.delenv(pairing.LIBRARY_VARIABLE, raising=False)
    monkeypatch.setattr(typesetter, "reason", lambda: "its own words")

    monkeypatch.setattr(typesetter, "kind", lambda: typesetter.Kind.READY)
    assert gate.library() == (gate.READY, "")

    monkeypatch.setattr(typesetter, "kind", lambda: typesetter.Kind.REFUSED)
    assert gate.library() == (
        gate.REFUSED,
        "the typesetting library was refused (its own words)",
    )

    monkeypatch.setenv(pairing.LIBRARY_VARIABLE, "/some/lib.so")
    monkeypatch.setattr(typesetter, "kind", lambda: typesetter.Kind.NOT_INSTALLED)
    assert gate.library() == (
        gate.NOT_INSTALLED,
        "the typesetting library is not installed (its own words)",
    )

    monkeypatch.setattr(typesetter, "kind", lambda: typesetter.Kind.UNSUPPORTED)
    assert gate.library() == (
        "unsupported",
        "the typesetting library is not in use (unsupported: its own words)",
    )


def test_the_kinds_the_gate_names_are_the_librarys_words_for_them() -> None:
    assert (gate.READY, gate.NOT_INSTALLED, gate.REFUSED, gate.FAILED) == tuple(
        typesetter.Kind[name].value for name in ("READY", "NOT_INSTALLED", "REFUSED", "FAILED")
    )


def test_the_gate_answers_in_a_sentence_whatever_asking_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def gone() -> Any:
        raise ModuleNotFoundError("No module named 'advtext'")

    def broken() -> Any:
        raise RuntimeError("the gate broke")

    monkeypatch.setattr(typesetter, "module", broken)
    kind, sentence = gate.library()
    assert kind == gate.FAILED and "RuntimeError: the gate broke" in sentence

    monkeypatch.setattr(gate, "_typesetter", gone)
    kind, sentence = gate.library()
    assert kind == gate.NOT_INSTALLED
    assert sentence.startswith("the text library is not installed (ModuleNotFoundError")
