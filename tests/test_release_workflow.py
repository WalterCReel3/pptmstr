"""
`.github/workflows/release.yml`: what can leave this repository, and when.

The workflow was written without being run on GitHub. These tests hold it to
everything that can be checked from inside a checkout, in the shape imscribe's
`tests/test_ci_workflow.py` holds its own release:

1.  It reads as YAML -- in a subset (below), because `./.venv` has no YAML
    parser and a dev dependency added for one test file is not worth it.
2.  It can send files to PyPI for a pushed tag `v*` and for nothing else, to
    TestPyPI only for a run started by hand that asks, and does neither before
    the build job and the install check passed; only the two publishing jobs
    hold `id-token: write`.
3.  The build job keeps the two files before anything installs the wheel, and
    every job that downloads them refuses them unless they are the bytes whose
    sha256 the build job took from make_dist; that check is run here.
4.  Every action is named by a 40-hex commit, one commit per action, with the
    version it was read as on a line above.
5.  The tag is compared with `__version__` in `pptmstr/__init__.py`, which is
    where `[tool.hatch.version]` reads it; the step's shell is run here.
6.  The checks of the installed wheel are run here, in a shell, against
    the report `pptmstr --version` writes and against stand-ins for the
    bundled CLI: green on the right answer, red on each wrong one; and the
    import of `pptmstr.app` it asks for is run here with no display.
7.  The system libraries it installs are `scripts/probe.py`'s list.

**The YAML subset.** `read_yaml` below is imscribe's reader, copied. It is not
a YAML parser. It reads block mappings, block sequences, plain and quoted
scalars on one line, and literal blocks (`|`), with comments on lines of their
own, and it REFUSES everything else by raising -- flow collections, anchors,
tags, folded blocks, tabs, a comment after a value, a plain scalar holding
`: ` or ` #`. When `yaml` is importable,
`test_the_reader_agrees_with_a_real_parser_when_there_is_one` compares the
two readings; when it is not, that test compares nothing and passes. Scalars
stay strings: `on` is a key named "on" here, where PyYAML makes it `True`;
`default: false` is the string "false".

**What this cannot see**: whether an action exists at the commit named,
whether the trusted publishers and environments exist, whether the runner's
Ubuntu has the package names, whether the pins resolve from PyPI on the day.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shlex
import stat
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import pptmstr
from pptmstr import pairing, version

_ROOT = Path(__file__).resolve().parent.parent
_RELEASE = _ROOT / ".github" / "workflows" / "release.yml"
_PYPROJECT = _ROOT / "pyproject.toml"
_MAKEFILE = _ROOT / "Makefile"

# The one action that is not GitHub's own, named by a commit and not a tag: it
# is handed the right to publish. v1.14.2, the commit imscribe's release.yml names.
PUBLISH_ACTION = "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33"

# The commits imscribe's release.yml names for the same actions, and the
# version each was read as.
PINNED = {
    "actions/checkout": ("3d3c42e5aac5ba805825da76410c181273ba90b1", "v7.0.1"),
    "actions/setup-python": ("5fda3b95a4ea91299a34e894583c3862153e4b97", "v7.0.0"),
    "actions/upload-artifact": ("cf430e030ddbb5b0abf93d22962f4752f3646cd9", "v7.0.2"),
    "actions/download-artifact": ("9000827ccba6bdab643e8b6fd33ac0654aef8333", "v8.0.2"),
    "pypa/gh-action-pypi-publish": ("dc37677b2e1c63e2034f94d8a5b11f265b73ba33", "v1.14.2"),
}


def _probe() -> ModuleType:
    """
    `scripts/probe.py` as a module, without putting `scripts/` on `sys.path`.
    """
    spec = importlib.util.spec_from_file_location("_probe_for_release", _ROOT / "scripts/probe.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Its dataclasses look their module up by name while the class is made.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# the reader


class NotInTheSubset(ValueError):
    """
    A line `read_yaml` will not guess at.
    """


_KEY = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_.-]*):(?: (.*))?$")


def _scalar(text: str, where: str) -> str | None:
    """
    One scalar from the rest of a line. `None` for an empty value.
    """
    if text == "":
        return None
    if text[0] == '"':
        if len(text) < 2 or text[-1] != '"' or '"' in text[1:-1] or "\\" in text:
            raise NotInTheSubset(f"{where}: a double-quoted scalar with an escape or a tail")
        return text[1:-1]
    if text[0] == "'":
        if len(text) < 2 or text[-1] != "'" or "'" in text[1:-1]:
            raise NotInTheSubset(f"{where}: a single-quoted scalar with a quote inside or a tail")
        return text[1:-1]
    if text[0] in "[{&*!|>%@`,?#-" and not re.match(r"-[^ ]", text):
        raise NotInTheSubset(f"{where}: {text[0]!r} starts something this reader does not read")
    if ": " in text or text.endswith(":") or " #" in text:
        raise NotInTheSubset(f"{where}: a plain scalar holding ': ' or ' #'; quote it")
    return text


def read_yaml(text: str) -> Any:
    """
    The mapping/sequence/string structure of `text`, or `NotInTheSubset`.
    """
    if "\t" in text:
        raise NotInTheSubset("a tab")
    lines: list[tuple[int, str, int]] = []  # indent, content, line number
    raw = text.split("\n")
    for number, line in enumerate(raw, start=1):
        if line != line.rstrip():
            raise NotInTheSubset(f"line {number}: trailing white space")
        lines.append((len(line) - len(line.lstrip(" ")), line.strip(), number))

    position = 0

    def skip() -> None:
        nonlocal position
        while position < len(lines) and (
            lines[position][1] == "" or lines[position][1].startswith("#")
        ):
            position += 1

    def literal(parent: int) -> str:
        """
        The lines of a `|` block: everything indented deeper than `parent`.
        """
        nonlocal position
        body: list[tuple[int, str]] = []
        while position < len(lines):
            indent, content, _ = lines[position]
            if content != "" and indent <= parent:
                break
            body.append((indent, content))
            position += 1
        while body and body[-1][1] == "":
            body.pop()
        if not body:
            raise NotInTheSubset("an empty literal block")
        base = min(indent for indent, content in body if content != "")
        return "".join(
            (" " * (indent - base) + content if content else "") + "\n" for indent, content in body
        )

    def value(rest: str | None, indent: int, where: str) -> Any:
        """
        What follows `key:` -- on the line, as a literal block, or nested.
        """
        if rest == "|":
            return literal(indent)
        if rest is not None and rest != "":
            return _scalar(rest, where)
        skip()
        if position < len(lines) and lines[position][0] > indent:
            return block(lines[position][0])
        return None

    def block(indent: int) -> Any:
        nonlocal position
        skip()
        if lines[position][1].startswith("- ") or lines[position][1] == "-":
            return sequence(indent)
        return mapping(indent, {})

    def mapping(indent: int, into: dict[str, Any]) -> dict[str, Any]:
        nonlocal position
        while True:
            skip()
            if position >= len(lines) or lines[position][0] < indent:
                return into
            at, content, number = lines[position]
            where = f"line {number}"
            if at != indent:
                raise NotInTheSubset(f"{where}: indented {at}, in a mapping at {indent}")
            found = _KEY.match(content)
            if found is None:
                raise NotInTheSubset(f"{where}: not `key: value`: {content!r}")
            key, rest = found.group(1), found.group(2)
            if key in into:
                raise NotInTheSubset(f"{where}: the key {key!r} twice in one mapping")
            position += 1
            into[key] = value(rest, indent, where)

    def sequence(indent: int) -> list[Any]:
        nonlocal position
        items: list[Any] = []
        while True:
            skip()
            if position >= len(lines) or lines[position][0] < indent:
                return items
            at, content, number = lines[position]
            where = f"line {number}"
            if at != indent or not content.startswith("- "):
                raise NotInTheSubset(f"{where}: not an item of the sequence at {indent}")
            inner = content[2:]
            if inner != inner.lstrip(" "):
                raise NotInTheSubset(f"{where}: more than one space after '-'")
            found = _KEY.match(inner)
            if found is None or inner[0] in "\"'":
                position += 1
                items.append(_scalar(inner, where))
                continue
            # `- key: value` opens a mapping whose keys sit two columns in.
            lines[position] = (indent + 2, inner, number)
            items.append(mapping(indent + 2, {}))

    skip()
    if position >= len(lines):
        raise NotInTheSubset("nothing but comments")
    if lines[position][0] != 0:
        raise NotInTheSubset(f"line {lines[position][2]}: the document starts indented")
    document = mapping(0, {})
    skip()
    if position != len(lines):
        raise NotInTheSubset(f"line {lines[position][2]}: not read")
    return document


# ---------------------------------------------------------------------------
# what the tests ask of the workflow


def _release() -> dict[str, Any]:
    document = read_yaml(_RELEASE.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    steps = job["steps"]
    assert isinstance(steps, list) and steps and all(isinstance(step, dict) for step in steps)
    return steps


def _step(job: dict[str, Any], prefix: str) -> dict[str, Any]:
    found = [step for step in _steps(job) if step["name"].startswith(prefix)]
    assert len(found) == 1, (prefix, [step["name"] for step in _steps(job)])
    return found[0]


def _commands(run: str) -> list[list[str]]:
    """
    A `run:` as shell words, one list per simple command; a backslash-newline joins lines.
    """
    joined = run.replace("\\\n", " ")
    commands: list[list[str]] = []
    for line in joined.splitlines():
        lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        current: list[str] = []
        for word in lexer:
            if word in {";", "&&", "||", "|", "{", "}", "&"}:
                if current:
                    commands.append(current)
                current = []
            else:
                current.append(word)
        if current:
            commands.append(current)
    return commands


def _apt_packages(job: dict[str, Any]) -> list[str]:
    """
    What a job's `sudo apt-get install` commands name, in order.
    """
    packages: list[str] = []
    for step in _steps(job):
        for command in _commands(step.get("run", "")):
            if command[:3] == ["sudo", "apt-get", "install"]:
                packages += [word for word in command[3:] if not word.startswith("-")]
    return packages


def _shell(run: str, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """
    A `run:` as GitHub's default shell for it runs it: bash, stopping at the first failure.
    """
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", run],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        cwd=str(cwd),
        env=env,
    )


# ---------------------------------------------------------------------------
# the reader itself


def test_the_reader_reads_the_shapes_the_workflow_uses() -> None:
    text = (
        "# a comment\n"
        "name: CI\n"
        "\n"
        "on:\n"
        "  push:\n"
        "    tags:\n"
        '      - "v*"\n'
        "  workflow_dispatch:\n"
        "jobs:\n"
        "  one:\n"
        "    if: github.event_name == 'push'\n"
        "    steps:\n"
        '      - name: "a: b"\n'
        "        run: |\n"
        "          first \\\n"
        "            second\n"
        "\n"
        "          third\n"
        "\n"
        "      # v1.2.3\n"
        "      - uses: actions/checkout@v4\n"
    )
    assert read_yaml(text) == {
        "name": "CI",
        "on": {"push": {"tags": ["v*"]}, "workflow_dispatch": None},
        "jobs": {
            "one": {
                "if": "github.event_name == 'push'",
                "steps": [
                    {"name": "a: b", "run": "first \\\n  second\n\nthird\n"},
                    {"uses": "actions/checkout@v4"},
                ],
            }
        },
    }


@pytest.mark.parametrize(
    "text",
    [
        "a: [1, 2]\n",
        "a: {b: c}\n",
        "a: &anchor b\n",
        "a: *alias\n",
        "a: !tag b\n",
        "a: >\n  folded\n",
        "a: |-\n  stripped\n",
        "a: b # a comment after a value\n",
        "a: b: c\n",
        "a: 'it''s'\n",
        'a: "an \\n escape"\n',
        "a:\tb\n",
        "a: b \n",
        "a: b\na: c\n",
        "a:\n  b: c\n   d: e\n",
        "  a: b\n",
        "a:\n  - b\n  c: d\n",
        "- a\n",
        "# only a comment\n",
    ],
)
def test_the_reader_refuses_what_it_would_have_to_guess_at(text: str) -> None:
    with pytest.raises(NotInTheSubset):
        read_yaml(text)


def test_the_reader_agrees_with_a_real_parser_when_there_is_one() -> None:
    """
    With PyYAML importable, its reading and this file's are the same structure.

    Where it is not importable this compares nothing and passes: a skip would
    change the gate's count for a check that cannot run there. PyYAML reads
    the bare key `on` as `True` and `false` as a boolean; both sides are
    brought to strings before comparing.
    """
    ours = _release()
    if importlib.util.find_spec("yaml") is None:
        return
    import yaml  # type: ignore[import-untyped,unused-ignore]

    def strings(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                ("on" if key is True else str(key)): strings(item) for key, item in node.items()
            }
        if isinstance(node, list):
            return [strings(item) for item in node]
        if isinstance(node, bool):
            return "true" if node else "false"
        return node if node is None else str(node)

    assert strings(yaml.safe_load(_RELEASE.read_text(encoding="utf-8"))) == ours


# ---------------------------------------------------------------------------
# when, and to where


def test_the_release_starts_for_a_pushed_tag_or_by_hand_and_for_no_branch() -> None:
    on = _release()["on"]
    assert set(on) == {"push", "workflow_dispatch"}
    assert on["push"] == {"tags": ["v*"]}, "a branch push must not start a release"
    assert on["workflow_dispatch"]["inputs"]["testpypi"]["default"] == "false"
    assert on["workflow_dispatch"]["inputs"]["testpypi"]["type"] == "boolean"
    # Nothing is granted to the workflow as a whole but reading the repository.
    assert _release()["permissions"] == {"contents": "read"}


def test_only_the_two_publishing_jobs_can_publish_and_each_only_for_its_own_event() -> None:
    """
    PyPI: a PUSHED tag `v...`, nothing else. TestPyPI: a run started by hand
    with the input set, nothing else. Each after the build job, each in its
    own environment, each with `id-token: write` and no other write.
    """
    jobs = _release()["jobs"]
    assert set(jobs) == {"build", "install-check", "publish-pypi", "publish-testpypi"}
    uploads = {
        name: [step for step in _steps(job) if step.get("uses") == PUBLISH_ACTION]
        for name, job in jobs.items()
    }
    assert {name: len(steps) for name, steps in uploads.items()} == {
        "build": 0,
        "install-check": 0,
        "publish-pypi": 1,
        "publish-testpypi": 1,
    }
    for name in ("build", "install-check"):
        assert "permissions" not in jobs[name] and "environment" not in jobs[name], name
        assert "if" not in jobs[name], name
    assert jobs["install-check"]["needs"] == "build"

    pypi = jobs["publish-pypi"]
    assert pypi["if"] == "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')"
    assert pypi["environment"] == {"name": "pypi", "url": "https://pypi.org/project/pptmstr/"}
    assert "repository-url" not in uploads["publish-pypi"][0]["with"]

    rehearsal = jobs["publish-testpypi"]
    assert rehearsal["if"] == (
        "github.event_name == 'workflow_dispatch' && github.event.inputs.testpypi == 'true'"
    )
    assert rehearsal["environment"] == {
        "name": "testpypi",
        "url": "https://test.pypi.org/project/pptmstr/",
    }
    assert uploads["publish-testpypi"][0]["with"]["repository-url"] == (
        "https://test.pypi.org/legacy/"
    )

    for name in ("publish-pypi", "publish-testpypi"):
        job = jobs[name]
        assert job["needs"] == ["build", "install-check"], name
        assert job["permissions"] == {"contents": "read", "id-token": "write"}, name
        assert job["runs-on"].startswith("ubuntu-"), "the publishing action runs on Linux only"
        assert uploads[name][0]["with"]["packages-dir"] == "upload", name
        # No token anywhere: Trusted Publishing is the run's own identity.
        assert "password" not in uploads[name][0]["with"], name
    text = _RELEASE.read_text(encoding="utf-8")
    assert "secrets." not in text and "PYPI_API_TOKEN" not in text
    assert text.count("id-token: write") == 2


def test_the_release_says_what_a_second_run_of_one_version_does() -> None:
    """
    `skip-existing` finishes an upload that stopped partway; it also lets a
    run for a version the index already holds go green having sent nothing.
    The file says so where the owner reads before pushing a tag.
    """
    jobs = _release()["jobs"]
    for name in ("publish-pypi", "publish-testpypi"):
        upload = next(step for step in _steps(jobs[name]) if step.get("uses") == PUBLISH_ACTION)
        assert upload["with"]["skip-existing"] == "true", name
    said = " ".join(
        line.lstrip("# ")
        for line in _RELEASE.read_text(encoding="utf-8").splitlines()
        if line.startswith("#")
    )
    assert "GOES GREEN HAVING UPLOADED NOTHING" in said
    assert "`skip-existing: true`" in said
    assert "WRITTEN WITHOUT BEING RUN ON GITHUB" in said


# ---------------------------------------------------------------------------
# every step


def test_a_run_step_is_a_command_a_person_can_paste() -> None:
    """
    No workflow expression inside a `run:`; values arrive through `env:`.
    """
    for name, job in _release()["jobs"].items():
        for step in _steps(job):
            assert "${{" not in step.get("run", ""), (name, step["name"])


def test_every_action_is_named_by_a_commit() -> None:
    for name, job in _release()["jobs"].items():
        for step in _steps(job):
            assert ("run" in step) != ("uses" in step), (name, step)
            assert "name" in step, (name, step)
            if "uses" in step:
                assert re.fullmatch(r"[a-z-]+/[a-z-]+@[0-9a-f]{40}", step["uses"]), step


def test_each_action_is_the_commit_imscribe_names_and_says_which_version_it_is() -> None:
    """
    One action, one commit, wherever it is used in the file; and one of the
    two lines above each `uses` (the step's name may come between) says the
    version the commit was read as, because a hash alone tells a reader nothing.
    """
    lines = _RELEASE.read_text(encoding="utf-8").splitlines()
    commits: dict[str, set[str]] = {}
    for at, line in enumerate(lines):
        found = re.fullmatch(r"\s+uses: (\S+)@(\S+)", line)
        if found is None:
            continue
        action, commit = found.group(1), found.group(2)
        commits.setdefault(action, set()).add(commit)
        assert f"# {PINNED[action][1]}" in [one.strip() for one in lines[at - 2 : at]], action
    assert {action: found for action, found in commits.items()} == {
        action: {commit} for action, (commit, _) in PINNED.items()
    }


def test_the_checkout_leaves_no_token_behind() -> None:
    jobs = _release()["jobs"]
    # The install check needs nothing of the repository; only the build checks it out.
    assert not any(
        step.get("uses", "").startswith("actions/checkout@")
        for step in _steps(jobs["install-check"])
    )
    build = jobs["build"]
    checkout = _step(build, "Check out")
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"] == {"persist-credentials": "false"}


# ---------------------------------------------------------------------------
# the build job


def test_the_build_is_what_make_dist_runs_on_python_3_11_and_keeps_two_files() -> None:
    build = _release()["jobs"]["build"]
    assert _step(build, "Python")["with"] == {"python-version": "3.11"}
    requires = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))["project"]
    assert requires["requires-python"].startswith(">=3.11")
    # What `make dist` runs, with the runner's interpreter (the recipe names
    # ./.venv, which a runner does not have).
    recipe = _MAKEFILE.read_text(encoding="utf-8")
    assert '$(PY) -P scripts/make_dist.py --out "$(DIST_DIR)" --rev "$(DIST_REV)"' in recipe
    built = _step(build, "Build the commit twice")
    assert built["run"] == "python -P scripts/make_dist.py --out dist --rev HEAD"
    tools = _step(build, "The tools that build and check")
    assert _commands(tools["run"])[0][:4] == ["python", "-m", "pip", "install"]
    # Only the wheel and the sdist: dist/ also holds .sha256 files and
    # BUILT-FROM, which an upload of the directory would stop on.
    files = _step(build, "The two files")
    assert "cp dist/*.whl dist/*.tar.gz upload/" in files["run"]
    assert files["id"] == "files"
    assert build["outputs"] == {"sha256": "${{ steps.files.outputs.sha256 }}"}
    assert _step(build, "Whether PyPI")["run"] == "python -m twine check --strict upload/*"
    kept = _step(build, "Keep the two files")
    assert kept["uses"].startswith("actions/upload-artifact@")
    assert kept["with"] == {"name": "dist", "path": "upload", "if-no-files-found": "error"}
    # Tag, build, hash, twine check, keep -- and keeping is the last step.
    names = [step["name"] for step in _steps(build)]
    order = [
        names.index(_step(build, prefix)["name"])
        for prefix in (
            "The tag is the version",
            "Build the commit twice",
            "The two files",
            "Whether PyPI",
            "Keep the two files",
        )
    ]
    assert order == sorted(order)
    assert order[-1] == len(names) - 1


def test_nothing_in_the_build_job_installs_or_runs_what_it_built() -> None:
    """
    The wheel's dependencies, its native library and the bundled claude are
    code from the index; none of it runs where the kept files are made.
    """
    build = _release()["jobs"]["build"]
    for step in _steps(build):
        run = step.get("run", "")
        for word in ("upload/*.whl", "venv", "bin/pptmstr", "claude", "apt-get", "import pptmstr"):
            if word == "upload/*.whl" and "pip install" not in run:
                continue
            assert word not in run, (step["name"], word)


def test_every_job_that_downloads_the_files_checks_them_before_anything_else() -> None:
    """
    install-check and both publish jobs: the download, then the sha256 check
    against the build job's output, then everything else.
    """
    jobs = _release()["jobs"]
    for name in ("install-check", "publish-pypi", "publish-testpypi"):
        steps = _steps(jobs[name])
        fetched, checked = steps[0], steps[1]
        assert fetched["uses"].startswith("actions/download-artifact@"), name
        assert fetched["with"] == {"name": "dist", "path": "upload"}, name
        assert checked["name"] == "The two files are the bytes make_dist checked", name
        assert checked["env"] == {"BUILT_SHA256": "${{ needs.build.outputs.sha256 }}"}, name
        assert checked["run"] == _step(jobs["install-check"], "The two files are")["run"], name
        assert not any(
            step.get("uses", "").startswith("actions/download-artifact@") for step in steps[1:]
        ), name


def _built_output(build_run: str, where: Path) -> str:
    """
    Run the build job's "The two files" step over a stand-in `dist/` and
    return the value it wrote for the output `sha256`.
    """
    dist = where / "dist"
    dist.mkdir()
    for name, body in (
        ("pptmstr-0.1.0-py3-none-any.whl", b"wheel"),
        ("pptmstr-0.1.0.tar.gz", b"sdist"),
    ):
        (dist / name).write_bytes(body)
        done = subprocess.run(
            ["sha256sum", name], cwd=dist, capture_output=True, text=True, check=True
        )
        (dist / f"{name}.sha256").write_text(done.stdout, encoding="utf-8")
    (dist / "BUILT-FROM").write_text("stand-in\n", encoding="utf-8")
    output = where / "github_output"
    done = _shell(build_run, where, {"PATH": "/usr/bin:/bin", "GITHUB_OUTPUT": str(output)})
    assert done.returncode == 0, done.stderr
    found = re.fullmatch(
        r"sha256<<(\S+)\n(.*)\n\1\n", output.read_text(encoding="utf-8"), re.DOTALL
    )
    assert found is not None, output.read_text(encoding="utf-8")
    return found.group(2)


def test_the_hashes_the_build_publishes_are_what_the_downloaders_check(tmp_path: Path) -> None:
    """
    The build step's output, handed to the check every downloading job runs:
    green on the same two files, red on a changed byte, an extra file, a
    missing file, and an empty output.
    """
    jobs = _release()["jobs"]
    built = tmp_path / "built"
    built.mkdir()
    sums = _built_output(_step(jobs["build"], "The two files")["run"], built)
    assert len(sums.splitlines()) == 2
    # A file that is not what make_dist's .sha256 says stops the build step.
    broken = tmp_path / "broken"
    (broken / "dist").mkdir(parents=True)
    for name, body in (
        ("pptmstr-0.1.0-py3-none-any.whl", b"wheel"),
        ("pptmstr-0.1.0.tar.gz", b"sdist"),
    ):
        (broken / "dist" / name).write_bytes(body)
        (broken / "dist" / f"{name}.sha256").write_text(f"{'0' * 64}  {name}\n", encoding="utf-8")
    done = _shell(
        _step(jobs["build"], "The two files")["run"],
        broken,
        {"PATH": "/usr/bin:/bin", "GITHUB_OUTPUT": str(broken / "out")},
    )
    assert done.returncode != 0
    assert not (broken / "out").exists()

    check = _step(jobs["install-check"], "The two files are")["run"]

    def judge(case: str, files: dict[str, bytes], given: str) -> int:
        where = tmp_path / case
        (where / "upload").mkdir(parents=True)
        for name, body in files.items():
            (where / "upload" / name).write_bytes(body)
        return _shell(check, where, {"PATH": "/usr/bin:/bin", "BUILT_SHA256": given}).returncode

    good = {"pptmstr-0.1.0-py3-none-any.whl": b"wheel", "pptmstr-0.1.0.tar.gz": b"sdist"}
    assert judge("good", good, sums) == 0
    assert judge("changed", {**good, "pptmstr-0.1.0.tar.gz": b"sdisT"}, sums) != 0
    assert judge("extra", {**good, "extra.whl": b"x"}, sums) != 0
    assert judge("missing", {"pptmstr-0.1.0.tar.gz": b"sdist"}, sums) != 0
    assert judge("empty", good, "") != 0
    assert judge("one line", good, sums.splitlines()[0]) != 0


def test_the_install_check_installs_asks_and_imports_in_that_order() -> None:
    check = _release()["jobs"]["install-check"]
    names = [step["name"] for step in _steps(check)]
    assert _step(check, "Python")["with"] == {"python-version": "3.11"}
    order = [
        names.index(_step(check, prefix)["name"])
        for prefix in (
            "The two files are",
            "Python",
            "System libraries",
            "Install the wheel",
            "The installed app imports",
            "The claude CLI",
        )
    ]
    assert order == sorted(order)


def test_the_app_import_the_install_check_asks_for_needs_no_display() -> None:
    """
    The step's import, run with this interpreter over this checkout with no
    display in the environment: it exits 0, so on a headless runner the step
    is red only when the app cannot be imported.
    """
    step = _step(_release()["jobs"]["install-check"], "The installed app imports")
    found = re.search(r'bin/python -P -c "([^"]+)"', step["run"])
    assert found is not None and found.group(1) == "import pptmstr.app"
    env = {
        key: value for key, value in os.environ.items() if key not in {"DISPLAY", "WAYLAND_DISPLAY"}
    }
    env["PYTHONPATH"] = str(_ROOT)
    done = subprocess.run(
        [sys.executable, "-P", "-c", f"{found.group(1)}; import pptmstr; print(pptmstr.__file__)"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
    )
    assert done.returncode == 0, done.stderr
    assert Path(done.stdout.strip()).parent == _ROOT / "pptmstr"


def test_the_system_libraries_are_the_probes_list() -> None:
    """
    `scripts/probe.py`'s X11_RUNTIME_LIBS is the one list of what the
    imgui-bundle wheel loads from the system; the workflow installs its apt
    column, every package of it and nothing more.
    """
    assert _apt_packages(_release()["jobs"]["build"]) == []
    packages = _apt_packages(_release()["jobs"]["install-check"])
    assert len(packages) == len(set(packages))
    assert set(packages) == {lib.apt for lib in _probe().X11_RUNTIME_LIBS}
    assert {"libx11-6", "libgl1"} <= set(packages)


def test_the_version_is_read_where_hatchling_reads_it() -> None:
    project = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    assert project["project"]["dynamic"] == ["version"]
    assert "version" not in project["project"]
    assert project["tool"]["hatch"]["version"] == {"path": "pptmstr/__init__.py"}


def test_a_tag_that_is_not_the_version_is_refused_before_anything_is_built() -> None:
    """
    The step's `sed` against the real `pptmstr/__init__.py`, run in a shell
    for the right tag and for wrong ones.
    """
    build = _release()["jobs"]["build"]
    step = _step(build, "The tag is the version")
    assert step["if"] == "github.event_name == 'push'"
    assert step["env"] == {"RELEASE_TAG": "${{ github.ref_name }}"}
    assert "pptmstr/__init__.py" in step["run"] and "pyproject.toml" not in step["run"]

    def run(tag: str) -> int:
        done = _shell(step["run"], _ROOT, {"PATH": "/usr/bin:/bin", "RELEASE_TAG": tag})
        if done.returncode == 0:
            said = f"pptmstr/__init__.py says {pptmstr.__version__}; the tag is {tag}"
            assert said in done.stdout
        return done.returncode

    stated = pptmstr.__version__
    assert run(f"v{stated}") == 0
    assert run(stated) != 0, "a tag without the v"
    assert run(f"v{stated}.post1") != 0
    assert run(f"v{stated}"[:-1]) != 0
    assert run("v") != 0


# ---------------------------------------------------------------------------
# the installed wheel, asked


def _installed_report(monkeypatch: pytest.MonkeyPatch) -> str:
    """
    What `pptmstr --version` prints from an install: this process's report,
    with the package taken for an installed one (no checkout around it).
    """
    monkeypatch.setattr(pairing, "checkout", lambda: None)
    return version.report(pairing.adopt()) + "\n"


def _version_checks(step: dict[str, Any]) -> str:
    """
    The step's lines that judge `version.txt`: everything after it is printed.
    """
    run = str(step["run"])
    head, marker, checks = run.partition("cat version.txt\n")
    assert marker and "bin/pptmstr --version > version.txt" in head
    assert "pip check" in head and "pip install upload/*.whl" in head
    return checks


def _judge(checks: str, report: str, where: Path) -> int:
    (where / "version.txt").write_text(report, encoding="utf-8")
    return _shell(checks, where, {"PATH": "/usr/bin:/bin"}).returncode


def test_the_install_check_reads_the_report_and_not_the_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    `pptmstr --version` exits 0 whatever it finds, so the step reads its lines.

    This process's own report, made as an installed one, is green exactly when
    its typeset line says the library is ready (`IMSCRIBE_NO_TYPESET=1` runs
    make it red, which is what the step must do on a runner without the
    library). Each way the report can name a wrong install is red.
    """
    assert version.main.__doc__ is not None and "Always 0" in version.main.__doc__
    checks = _version_checks(_step(_release()["jobs"]["install-check"], "Install the wheel"))
    report = _installed_report(monkeypatch)
    ready = f"beside this wheel ({importlib.import_module('pptmstr.ui.typeset.gate').READY})"
    assert (_judge(checks, report, tmp_path) == 0) == (ready in report), report

    good = (
        "pptmstr 0.1.0 (installed at /x/site-packages/pptmstr)\n"
        "python 3.11.2 (/x/bin/python)\n"
        "imgui-bundle 1.92.900, the version pptmstr pins\n"
        "imscribe 0.2.0, the version pptmstr pins: imported from /x, installed by name\n"
        "imtypeset 0.4.3.192900, the version pptmstr pins: library 0.4.3 at /x\n"
        "typeset text: the library is accepted beside this wheel (ready); its fonts are loaded\n"
    )
    assert _judge(checks, good, tmp_path) == 0
    wrong = {
        "a checkout": good.replace(
            "(installed at /x/site-packages/pptmstr)", "(a source checkout at /x)"
        ),
        "another imgui-bundle": good.replace(
            "imgui-bundle 1.92.900, the version pptmstr pins",
            "imgui-bundle 1.92.901 is running and pptmstr pins 1.92.900",
        ),
        "no imscribe": good.replace(
            "imscribe 0.2.0, the version pptmstr pins: imported from /x, installed by name",
            "imscribe: not installed (no module); pptmstr pins 0.2.0",
        ),
        "no library": good.replace(
            "the library is accepted beside this wheel (ready); its fonts are loaded",
            "not in use (absent): no library",
        ),
        "nothing": "",
    }
    for why, report in wrong.items():
        assert _judge(checks, report, tmp_path) != 0, why


def test_the_install_checks_match_the_sentences_the_report_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The fixed strings the step greps for are the ones `pptmstr.version` writes,
    so a reworded report turns this red rather than the release.
    """
    monkeypatch.setattr(pairing, "checkout", lambda: None)
    assert version._pptmstr().startswith(f"pptmstr {pptmstr.__version__} (installed at ")
    assert version._against("1", "1") == "1, the version pptmstr pins"
    source = Path(version.__file__).read_text(encoding="utf-8")
    assert 'f"typeset text: the library is accepted beside this wheel ({kind}); "' in source


def _fake_install(where: Path, said: str, code: int = 0) -> None:
    """
    `installed/bin/python` that answers with a CLI path, and a CLI there that says `said`.
    """
    bin_dir = where / "installed" / "bin"
    bin_dir.mkdir(parents=True)
    cli = where / "claude"
    cli.write_text(f"#!/bin/sh\necho {shlex.quote(said)}\nexit {code}\n", encoding="utf-8")
    python = bin_dir / "python"
    python.write_text(f"#!/bin/sh\necho {shlex.quote(str(cli))}\n", encoding="utf-8")
    for path in (cli, python):
        path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.mark.parametrize(
    ("said", "code", "green"),
    [
        ("2.1.300 (Claude Code)", 0, True),
        # A stripped binary: the Bun runtime the CLI is built on, exit 0.
        ("1.4.3", 0, False),
        ("", 0, False),
        ("2.1.300 (Claude Code)", 1, False),
    ],
)
def test_the_bundled_cli_check_wants_claude_code_and_not_a_zero_exit(
    tmp_path: Path, said: str, code: int, green: bool
) -> None:
    step = _step(_release()["jobs"]["install-check"], "The claude CLI")
    assert _probe().CLAUDE_CODE_MARKER in step["run"]
    assert "claude_agent_sdk" in step["run"] and '"_bundled", "claude"' in step["run"]
    _fake_install(tmp_path, said, code)
    done = _shell(step["run"], tmp_path, {"PATH": "/usr/bin:/bin"})
    assert (done.returncode == 0) == green, (done.stdout, done.stderr)


def test_the_cli_locator_names_the_path_the_sdk_bundles() -> None:
    """
    The step's one-liner, run with this interpreter: the path it prints is the
    one `scripts/probe.py` checks, under the installed `claude_agent_sdk`.
    """
    step = _step(_release()["jobs"]["install-check"], "The claude CLI")
    code = re.search(r"bin/python -P -c '([^']+)'", step["run"])
    assert code is not None
    done = subprocess.run(
        [sys.executable, "-P", "-c", code.group(1)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ},
    )
    assert done.returncode == 0, done.stderr
    import claude_agent_sdk

    expected = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    assert Path(done.stdout.strip()) == expected
