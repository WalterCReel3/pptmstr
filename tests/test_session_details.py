"""
Details: what the modal says about a session or a sub-agent, where each value comes
from, and what the copy control puts on the clipboard.

The hazard is a value that reads as a fact and is not one -- a gate shown for a
session nothing is running, a transcript path nobody reported, a containment
answer for a card this window holds no session for. So most of this file is about
the rows that say "not known" and the notes that say where a value came from.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from imgui_bundle import imgui

from pptmstr import boardlog, boardsync, brief, sessionfile
from pptmstr.approval import Policy
from pptmstr.intents import (
    AgentFinished,
    AgentSpawned,
    ContextPolled,
    SessionRecalled,
    StateChanged,
    UsageAccrued,
)
from pptmstr.model import (
    AgentState,
    ContextSnapshot,
    EndedHow,
    NodeId,
    Recalled,
    TranscriptPresence,
    UsageRollup,
)
from pptmstr.settings import Settings
from pptmstr.store import Store
from pptmstr.ui import board_pane, launcher, session_details, widgets
from pptmstr.ui.session_details import NOT_KNOWN, Held, Looked, Row, View

ROOT: NodeId = ("s1", None)
BUILDER: NodeId = ("s1", "agent-b")

NOW = 500.0
WALL = 1_760_000_000.0


def _store(
    *,
    cwd: str | None = "/x/keys",
    template: str | None = "team",
    brief_dir: str | None = "/x/briefs/s1",
    policy: Policy = Policy.STRICT,
) -> Store:
    store = Store()
    store.apply(
        AgentSpawned(
            node_id=ROOT,
            parent=None,
            task="rotate the\nsigning key",
            model="claude-opus-5",
            started_at=100.0,
            cwd=cwd,
            session_base=cwd,
            template=template,
            brief=brief_dir,
            policy=policy,
        ),
        now=100.0,
    )
    store.apply(StateChanged(ROOT, AgentState.THINKING, "reading keys.py"), now=110.0)
    store.apply(
        AgentSpawned(
            node_id=BUILDER,
            parent=ROOT,
            task="builder",
            model="claude-haiku-5",
            started_at=120.0,
            agent_type="Builder",
        ),
        now=120.0,
    )
    store.apply(StateChanged(BUILDER, AgentState.RUNNING_TOOL, "running pytest"), now=130.0)
    store.apply(
        UsageAccrued(
            ROOT,
            UsageRollup(
                input_tokens=12_000,
                output_tokens=3_400,
                cache_creation_input_tokens=50,
                cache_read_input_tokens=900_000,
                total_cost_usd=1.5,
            ),
        ),
        now=131.0,
    )
    return store


def _see(store: Store, node: NodeId = ROOT, **kwargs: object) -> View:
    given: dict[str, object] = {
        "held": None,
        "looked": None,
        "logging": None,
        "now": NOW,
        "wall_now": WALL,
    }
    given.update(kwargs)
    shown = session_details.view(store.snapshot(), node, **given)  # type: ignore[arg-type]
    assert shown is not None
    return shown


def _row(shown: View, label: str) -> Row:
    row = shown.row(label)
    assert (
        row is not None
    ), f"no row {label!r} in {[r.label for s in shown.sections for r in s.rows]}"
    return row


def _labels(shown: View) -> list[str]:
    return [row.label for section in shown.sections for row in section.rows]


RUNNING = Held(policy=Policy.STRICT, contained=False, transcript=None, running=True)


# -- a live session -------------------------------------------------------------------


def test_a_live_session_says_what_it_is_where_it_runs_and_what_it_has_cost() -> None:
    shown = _see(_store(), held=RUNNING)

    assert shown.title == "rotate the signing key"
    assert _labels(shown) == [
        "session id",
        "task",
        "state",
        "team template",
        "model",
        "sub-agents",
        "directory",
        "git layout",
        "writes measured from",
        "gate",
        "contained",
        "started",
        "in this state since",
        "last active",
        "cost",
        "tokens",
        "context",
        "transcript",
        "brief",
        "session directory",
        "session file",
        "board log",
        "board recording",
    ]
    assert _row(shown, "session id") == Row("session id", "s1", copy="s1")
    assert _row(shown, "state").value == "thinking - reading keys.py"
    assert _row(shown, "team template").value == "team"
    assert _row(shown, "model").value == "claude-opus-5"
    assert _row(shown, "sub-agents").value == "1, 0 ended"
    assert _row(shown, "directory") == Row("directory", "/x/keys", copy="/x/keys")
    assert _row(shown, "writes measured from").copy == "/x/keys"
    assert _row(shown, "brief") == Row("brief", "/x/briefs/s1", copy="/x/briefs/s1")
    assert _row(shown, "cost").value == "$1.50"
    assert "not billing" in _row(shown, "cost").note
    assert _row(shown, "tokens").value == (
        "12,000 in, 3,400 out, 900,000 read from cache, 50 written to it"
    )


def test_a_task_typed_on_two_lines_is_drawn_on_one_and_copied_as_it_was_typed() -> None:
    task = _row(_see(_store()), "task")
    assert task.value == "rotate the signing key"
    assert task.copy == "rotate the\nsigning key"


def test_times_are_said_on_the_wall_clock_with_how_long_ago() -> None:
    import time

    shown = _see(_store())
    started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(WALL - 400.0))
    since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(WALL - 390.0))

    assert _row(shown, "started").value == f"{started} (6m40s ago)"
    assert _row(shown, "in this state since").value == f"{since} (6m30s ago)"
    assert shown.row("ended") is None


def test_when_an_agent_last_did_anything_is_not_known_and_is_said_not_to_be() -> None:
    """Nothing in the store records it, and the row exists so that is on screen."""
    for node in (ROOT, BUILDER):
        row = _row(_see(_store(), node), "last active")
        assert (row.value, row.known, row.copy) == (NOT_KNOWN, False, None)
        assert "when a state began" in row.note


# -- the gate, as granted -------------------------------------------------------------


def test_a_running_sessions_gate_is_what_it_answers_with_now() -> None:
    gate = _row(_see(_store(policy=Policy.STRICT), held=RUNNING), "gate")
    assert (gate.value, gate.copy) == ("strict", "strict")
    assert gate.note == "what it is answering with now"


def test_a_gate_that_has_moved_since_launch_says_both() -> None:
    """
    Launched autonomous and narrowed since. The record still says autonomous, and
    a modal that read only the record would show a gate that is not being applied.
    """
    held = Held(policy=Policy.STRICT, contained=True, transcript=None, running=True)
    gate = _row(_see(_store(policy=Policy.AUTONOMOUS), held=held), "gate")

    assert gate.value == "strict (launched under autonomous)"
    assert gate.copy == "strict"


def test_a_live_card_no_session_is_held_for_shows_the_grant_and_says_that_is_all_it_is() -> None:
    shown = _see(_store(policy=Policy.PERMISSIVE), held=None)
    gate, contained = _row(shown, "gate"), _row(shown, "contained")

    assert gate.value == "permissive"
    assert "as granted at launch" in gate.note and "no running session is held" in gate.note
    assert (contained.value, contained.known) == (NOT_KNOWN, False)
    assert "no session object" in contained.note


def test_a_session_the_pool_let_go_of_does_not_lend_its_last_gate_to_a_live_card() -> None:
    """
    ``running`` False on a card that has not ended: a relaunch held behind a run
    that is ending. The object's policy is not being applied to this row.
    """
    held = Held(policy=Policy.AUTONOMOUS, contained=True, transcript=None, running=False)
    gate = _row(_see(_store(policy=Policy.STRICT), held=held), "gate")

    assert gate.value == "strict"
    assert "as granted at launch" in gate.note


@pytest.mark.parametrize(("contained", "said"), [(True, "yes"), (False, "no")])
def test_whether_it_is_contained_is_the_session_objects_answer(contained: bool, said: str) -> None:
    held = Held(policy=Policy.STRICT, contained=contained, transcript=None, running=True)
    row = _row(_see(_store(), held=held), "contained")
    assert (row.value, row.known) == (said, True)


# -- an ended session, and a failed one -----------------------------------------------


def _ended(state: AgentState, error: str | None = None) -> Store:
    store = _store(policy=Policy.AUTONOMOUS)
    store.apply(AgentFinished(ROOT, state, 300.0, error=error), now=300.0)
    return store


def test_an_ended_session_shows_what_the_store_still_holds() -> None:
    import time

    held = Held(policy=Policy.STRICT, contained=True, transcript="/t/s1.jsonl", running=False)
    shown = _see(_ended(AgentState.CANCELLED), held=held)
    ended = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(WALL - 200.0))

    assert _row(shown, "state").value.startswith("cancelled")
    assert _row(shown, "ended").value == f"{ended} (3m20s ago)"
    assert shown.row("in this state since") is None
    assert _row(shown, "directory").copy == "/x/keys"
    assert _row(shown, "cost").value == "$1.50"
    assert _row(shown, "contained").value == "yes"
    assert _row(shown, "transcript").copy == "/t/s1.jsonl"


def test_an_ended_sessions_gate_is_its_grant_and_never_the_objects_last_value() -> None:
    held = Held(policy=Policy.STRICT, contained=True, transcript=None, running=False)
    for answer in (held, None, Held(Policy.PERMISSIVE, True, None, True)):
        gate = _row(_see(_ended(AgentState.DONE), held=answer), "gate")
        assert gate.value == "autonomous"
        assert gate.note == "as granted at launch; the session is not running"


def test_a_failed_session_shows_its_error_whole_on_the_clipboard() -> None:
    shown = _see(_ended(AgentState.FAILED, "the CLI exited 1\nwith no output"))
    error = _row(shown, "error")

    assert error.value == "the CLI exited 1 with no output"
    assert error.copy == "the CLI exited 1\nwith no output"
    assert _labels(shown).index("error") == _labels(shown).index("state") + 1


def test_a_failure_with_no_detail_says_none_was_recorded() -> None:
    error = _row(_see(_ended(AgentState.FAILED)), "error")
    assert (error.value, error.known, error.copy) == (NOT_KNOWN, False, None)


def test_a_session_that_did_not_fail_has_no_error_row() -> None:
    assert _see(_store()).row("error") is None
    assert _see(_ended(AgentState.CANCELLED)).row("error") is None


# -- what is not known ----------------------------------------------------------------


def test_every_value_the_window_does_not_hold_is_not_known_with_a_reason_and_no_copy() -> None:
    shown = _see(_store(cwd=None, template=None, brief_dir=None))
    unknown = {
        row.label: row.note for section in shown.sections for row in section.rows if not row.known
    }

    assert sorted(unknown) == [
        "board log",
        "board recording",
        "contained",
        "context",
        "directory",
        "git layout",
        "last active",
        "session directory",
        "session file",
        "team template",
        "transcript",
        "writes measured from",
    ]
    assert all(unknown.values()), "each says why"
    for section in shown.sections:
        for row in section.rows:
            if not row.known:
                assert (row.value, row.copy) == (NOT_KNOWN, None), row.label


def test_a_session_launched_with_only_its_task_has_no_brief_and_that_is_a_fact() -> None:
    row = _row(_see(_store(brief_dir=None)), "brief")
    assert (row.value, row.known, row.copy) == ("none", True, None)


def test_the_context_row_reads_the_poll_and_says_it_is_one() -> None:
    store = _store()
    store.apply(
        ContextPolled(
            ROOT,
            ContextSnapshot(
                used_tokens=60_000,
                max_tokens=180_000,
                raw_max_tokens=200_000,
                percentage=33.0,
                auto_compact_enabled=True,
                auto_compact_threshold=160_000,
                model="claude-opus-5",
                polled_at=140.0,
            ),
        ),
        now=140.0,
    )
    row = _row(_see(store), "context")

    assert row.value == "60,000 of 180,000 resident; 100,000 before it compacts"
    assert row.known and "polled" in row.note


def test_a_context_with_no_threshold_does_not_borrow_the_window_size_for_one() -> None:
    store = _store()
    store.apply(
        ContextPolled(
            ROOT,
            ContextSnapshot(
                used_tokens=60_000,
                max_tokens=180_000,
                raw_max_tokens=200_000,
                percentage=33.0,
                auto_compact_enabled=False,
                auto_compact_threshold=None,
                model="claude-opus-5",
                polled_at=140.0,
            ),
        ),
        now=140.0,
    )
    assert "no compaction threshold" in _row(_see(store), "context").value


# -- the transcript -------------------------------------------------------------------


def test_the_path_the_cli_reported_is_the_transcript_and_is_said_to_be() -> None:
    held = Held(policy=Policy.STRICT, contained=False, transcript="/t/s1.jsonl", running=True)
    looked = Looked(git_read=True, transcript="/derived/s1.jsonl", transcript_there=True)
    row = _row(_see(_store(), held=held, looked=looked), "transcript")

    assert row == Row(
        "transcript", "/t/s1.jsonl", copy="/t/s1.jsonl", note="as its CLI reported it"
    )


@pytest.mark.parametrize("there", [True, False])
def test_a_transcript_nobody_reported_is_the_derived_path_and_says_it_is_derived(
    there: bool,
) -> None:
    looked = Looked(git_read=True, transcript="/derived/s1.jsonl", transcript_there=there)
    row = _row(_see(_store(), held=RUNNING, looked=looked), "transcript")

    assert row.copy == "/derived/s1.jsonl"
    assert "where the CLI's naming puts it" in row.note
    assert ("no file is there" in row.note) is (not there)


def test_the_derived_path_is_where_the_sdk_finds_the_sessions_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The naming is the SDK's own function and is not respelled here, so the check is
    against the SDK's reader: a file written at the derived path is the file its
    lookup for that session and directory returns.
    """
    from claude_agent_sdk._internal import sessions as sdk

    config = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setattr(brief, "legacy_root", lambda: config / "projects")
    work = tmp_path / "some work" / "tree_1"
    work.mkdir(parents=True)
    session_id = "0b5f3a0e-6a3c-4b53-9c62-0d0f4f6f8d11"

    derived = session_details.transcript_for(str(work), session_id)
    derived.parent.mkdir(parents=True)
    derived.write_text("{}\n", encoding="utf-8")

    assert sdk._resolve_session_file_path(session_id, str(work)) == derived


# -- the one look at the disk ---------------------------------------------------------


def _rec(store: Store, node: NodeId = ROOT) -> object:
    return store.snapshot().nodes[node]


def test_the_look_reads_the_directorys_git_layout_and_whether_the_transcript_is_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(brief, "legacy_root", lambda: tmp_path / "projects")
    work = tmp_path / "work"
    work.mkdir()
    store = _store(cwd=str(work))

    looked = session_details.look(_rec(store), None)  # type: ignore[arg-type]
    assert looked.git_read and looked.git is None
    assert looked.transcript == str(session_details.transcript_for(str(work), "s1"))
    assert not looked.transcript_there
    assert looked.agent_transcript is None

    Path(looked.transcript).parent.mkdir(parents=True)
    Path(looked.transcript).write_text("{}\n", encoding="utf-8")
    assert session_details.look(_rec(store), None).transcript_there  # type: ignore[arg-type]


def test_the_look_names_a_linked_worktree_and_details_says_whose(tmp_path: Path) -> None:
    main = tmp_path / "main"
    (main / ".git" / "worktrees" / "feature").mkdir(parents=True)
    (main / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (main / ".git" / "objects").mkdir()
    (main / ".git" / "refs").mkdir()
    link = main / ".git" / "worktrees" / "feature"
    (link / "commondir").write_text("../..\n", encoding="utf-8")
    (link / "HEAD").write_text("ref: refs/heads/feature\n", encoding="utf-8")
    tree = tmp_path / "feature"
    tree.mkdir()
    (tree / ".git").write_text(f"gitdir: {link}\n", encoding="utf-8")
    store = _store(cwd=str(tree))

    looked = session_details.look(_rec(store), None)  # type: ignore[arg-type]
    assert isinstance(looked.git, launcher.LinkedWorktree), looked.git
    row = _row(_see(store, looked=looked), "git layout")

    assert row.value == f"a linked worktree (beside) of {main}, at its root"
    assert row.copy == str(main)
    assert row.note == "copies the main checkout"


def test_an_ordinary_directory_is_said_to_be_one() -> None:
    row = _row(_see(_store(), looked=Looked(git_read=True, git=None)), "git layout")
    assert row.known and row.value.startswith("ordinary")
    assert row.copy is None


def test_a_git_entry_that_could_not_be_read_is_not_known_and_names_the_entry() -> None:
    looked = Looked(git_read=True, git=launcher.UnreadGit("/x/keys/.git"))
    row = _row(_see(_store(), looked=looked), "git layout")
    assert (row.value, row.known) == (NOT_KNOWN, False)
    assert "/x/keys/.git" in row.note


def test_a_git_directory_outside_the_tree_is_named_and_copied() -> None:
    looked = Looked(git_read=True, git=launcher.OutsideGit("/elsewhere/.git"))
    row = _row(_see(_store(), looked=looked), "git layout")
    assert row.copy == "/elsewhere/.git" and "/elsewhere/.git" in row.value


def test_a_record_with_no_directory_is_not_looked_for_under_one() -> None:
    """
    Its transcript and its git layout are under the directory it does not have.
    Where pptmstr keeps its own files for the session is not.
    """
    looked = session_details.look(_rec(_store(cwd=None)), None)  # type: ignore[arg-type]

    assert not looked.git_read and looked.transcript is None
    assert looked == session_details._session_files("s1")
    assert looked.files_read and looked.session_dir == str(sessionfile.session_dir("s1"))


# -- a sub-agent ----------------------------------------------------------------------


def test_a_sub_agent_says_who_it_is_and_which_session_it_belongs_to() -> None:
    shown = _see(_store(), BUILDER, held=RUNNING)

    assert shown.title == "Builder"
    assert _labels(shown) == [
        "address",
        "agent id",
        "type",
        "state",
        "model",
        "session id",
        "session task",
        "directory",
        "started",
        "in this state since",
        "last active",
        "cost",
        "tokens",
        "transcript",
    ]
    assert _row(shown, "address").value == "builder", "the name its own bus answers to"
    assert _row(shown, "agent id") == Row("agent id", "agent-b", copy="agent-b")
    assert _row(shown, "type").value == "Builder"
    assert _row(shown, "state").value == "running - running pytest"
    assert _row(shown, "model").value == "claude-haiku-5", "its own, not its session's"
    assert _row(shown, "session id").copy == "s1"
    assert _row(shown, "session task").copy == "rotate the\nsigning key"
    assert _row(shown, "directory").copy == "/x/keys", "inherited from its session"


def test_a_sub_agent_has_no_gate_row_and_no_context_of_its_own() -> None:
    labels = _labels(_see(_store(), BUILDER, held=RUNNING))
    assert not {"gate", "contained", "context", "brief", "board log"} & set(labels)


def test_a_sub_agents_transcript_is_shown_only_when_the_file_is_there() -> None:
    path = "/t/s1/subagents/agent-agent-b.jsonl"
    there = _row(
        _see(_store(), BUILDER, looked=Looked(agent_transcript=path, agent_transcript_there=True)),
        "transcript",
    )
    absent = _row(
        _see(_store(), BUILDER, looked=Looked(agent_transcript=path)),
        "transcript",
    )
    never = _row(_see(_store(), BUILDER), "transcript")

    assert there == Row("transcript", path, copy=path)
    assert (absent.value, absent.copy, absent.known) == (NOT_KNOWN, None, False)
    assert path in absent.note and "nested deeper" in absent.note
    assert (never.value, never.known) == (NOT_KNOWN, False)


def test_the_look_finds_a_sub_agents_transcript_beside_the_one_the_cli_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Under the reported transcript when there is one: that is where the file is,
    and the derived path is only where the naming says it should be.
    """
    monkeypatch.setattr(brief, "legacy_root", lambda: tmp_path / "projects")
    work = tmp_path / "work"
    work.mkdir()
    store = _store(cwd=str(work))
    reported = tmp_path / "reported" / "s1.jsonl"
    under = reported.with_suffix("") / "subagents" / "agent-agent-b.jsonl"

    looked = session_details.look(_rec(store, BUILDER), str(reported))  # type: ignore[arg-type]
    assert looked.agent_transcript == str(under) and not looked.agent_transcript_there

    under.parent.mkdir(parents=True)
    under.write_text("{}\n", encoding="utf-8")
    looked = session_details.look(_rec(store, BUILDER), str(reported))  # type: ignore[arg-type]
    assert looked.agent_transcript_there

    derived = session_details.look(_rec(store, BUILDER), None)  # type: ignore[arg-type]
    assert derived.agent_transcript == str(
        session_details.transcript_for(str(work), "s1").with_suffix("")
        / "subagents"
        / "agent-agent-b.jsonl"
    )


def test_a_sub_agent_whose_session_record_is_gone_says_so() -> None:
    store = Store()
    store.apply(
        AgentSpawned(node_id=BUILDER, parent=None, task="builder", model="m", started_at=1.0),
        now=1.0,
    )
    row = _row(_see(store, BUILDER), "session task")
    assert (row.value, row.known) == (NOT_KNOWN, False)


def test_a_node_the_snapshot_does_not_have_has_no_view() -> None:
    assert (
        session_details.view(
            _store().snapshot(),
            ("nowhere", None),
            held=None,
            looked=None,
            logging=None,
            now=NOW,
            wall_now=WALL,
        )
        is None
    )


# -- the board log --------------------------------------------------------------------


@pytest.mark.parametrize(
    "logging",
    [
        board_pane.Logging(sessions={"s1": board_pane.LogState(directory="/logs/s1")}),
        board_pane.Logging(
            sessions={
                "s1": board_pane.LogState(
                    directory="/logs/s1", recording=False, stopped=True, why_not="disk full"
                )
            }
        ),
        board_pane.Logging(
            sessions={"s1": board_pane.LogState(directory="", recording=False, why_not="no id")}
        ),
        board_pane.Logging(off="switched off: board_log in settings.json"),
        board_pane.Logging(unread="not a boolean (/x/settings.json)"),
        board_pane.Logging(),
    ],
)
def test_the_recording_row_is_the_board_panes_own_line_for_the_same_state(
    logging: board_pane.Logging,
) -> None:
    """
    One wording, from one function: a Details that worded it again would be a
    second opinion on whether a board is being written down.
    """
    shown = _see(_store(), logging=logging)
    entry = logging.sessions.get("s1")
    line = board_pane.recording_line(logging.off, entry, logging.unread)
    assert line is not None

    assert _row(shown, "board recording").value == line.text
    log = _row(shown, "board log")
    if entry is not None and entry.directory:
        assert log == Row("board log", "/logs/s1", copy="/logs/s1")
    else:
        assert (log.value, log.known, log.copy) == (NOT_KNOWN, False, None)


def test_a_log_still_being_restored_says_so_and_claims_neither_answer() -> None:
    logging = board_pane.Logging(
        sessions={"s1": board_pane.LogState(directory="/logs/s1", recording=False, awaiting=True)}
    )
    row = _row(_see(_store(), logging=logging), "board recording")
    assert (row.value, row.known) == (NOT_KNOWN, False)
    assert "restore" in row.note


# -- what the application hands it ----------------------------------------------------


def _app_state(store: Store) -> object:
    from pptmstr import app

    state = app.AppState(store=store, bridge=MagicMock(), settings=Settings())
    state.frame_snap = store.snapshot()
    return state


def _session(policy: Policy, containment: str | None, transcript: str | None) -> MagicMock:
    session = MagicMock()
    session.policy = policy
    session.containment = containment
    session.transcript_path = transcript
    return session


def test_the_pools_session_is_read_live_and_is_running() -> None:
    from pptmstr import app

    state = _app_state(_store())
    state.pool = MagicMock()  # type: ignore[attr-defined]
    state.pool.session_for.return_value = _session(Policy.PERMISSIVE, "{}", "/t/s1.jsonl")  # type: ignore[attr-defined]
    state.ran["s1"] = _session(Policy.STRICT, None, None)  # type: ignore[attr-defined]

    assert app._held(state, BUILDER) == Held(  # type: ignore[arg-type]
        policy=Policy.PERMISSIVE, contained=True, transcript="/t/s1.jsonl", running=True
    )
    assert state.pool.session_for.call_args.args == (BUILDER,)  # type: ignore[attr-defined]


def test_a_session_the_pool_let_go_of_is_still_read_and_is_not_running() -> None:
    from pptmstr import app

    state = _app_state(_store())
    state.pool = MagicMock()  # type: ignore[attr-defined]
    state.pool.session_for.return_value = None  # type: ignore[attr-defined]
    state.ran["s1"] = _session(Policy.AUTONOMOUS, "{}", "/t/s1.jsonl")  # type: ignore[attr-defined]

    assert app._held(state, ROOT) == Held(  # type: ignore[arg-type]
        policy=Policy.AUTONOMOUS, contained=True, transcript="/t/s1.jsonl", running=False
    )


def test_a_card_no_session_object_exists_for_is_held_by_nothing() -> None:
    from pptmstr import app

    state = _app_state(_store())
    assert app._held(state, ROOT) is None  # type: ignore[arg-type]
    state.pool = MagicMock()  # type: ignore[attr-defined]
    state.pool.session_for.return_value = None  # type: ignore[attr-defined]
    assert app._held(state, ROOT) is None  # type: ignore[arg-type]


# -- the modal, in frames -------------------------------------------------------------


class _Modal:
    """
    ``app._draw_card_overlays`` in frames of a real Dear ImGui context, with the
    clipboard caught. ``at`` is where each copy control was last drawn, by the
    label of its row.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, store: Store) -> None:
        from pptmstr import app

        self.app = app
        self.store = store
        self.state = app.AppState(store=store, bridge=MagicMock(), settings=Settings())
        self.clipboard: list[str] = []
        self.at: dict[str, tuple[float, float]] = {}
        self.window: tuple[float, float, float, float] | None = None
        self._monkeypatch = monkeypatch

    def __enter__(self) -> _Modal:
        self._context = imgui.create_context()
        self.io = imgui.get_io()
        self.io.display_size = imgui.ImVec2(1280.0, 900.0)
        self.io.delta_time = 1.0 / 60.0
        self.io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
        self.io.set_ini_filename("")
        self.io.add_focus_event(True)
        at, clipboard = self.at, self.clipboard
        labels: list[str] = []
        outer = self

        def middle() -> tuple[float, float]:
            low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
            return ((low.x + high.x) / 2.0, (low.y + high.y) / 2.0)

        class Noting:
            def __getattr__(self, name: str) -> object:
                return getattr(imgui, name)

            @staticmethod
            def table_next_row() -> None:
                imgui.table_next_row()
                labels.append("")

            @staticmethod
            def text_colored(colour: object, text: str) -> None:
                if labels and not labels[-1]:
                    labels[-1] = text
                imgui.text_colored(colour, text)  # type: ignore[arg-type]

            @staticmethod
            def button(label: str) -> bool:
                pressed = imgui.button(label)
                at[label] = middle()
                pos, size = imgui.get_window_pos(), imgui.get_window_size()
                outer.window = (pos.x, pos.y, size.x, size.y)
                return pressed

            @staticmethod
            def set_clipboard_text(text: str) -> None:
                clipboard.append(text)

        real_small_button = widgets.small_button

        def small_button(label: str) -> bool:
            pressed = real_small_button(label)
            at[labels[-1]] = middle()
            return pressed

        self._monkeypatch.setattr(session_details, "imgui", Noting())
        self._monkeypatch.setattr(widgets, "small_button", small_button)
        return self

    def __exit__(self, *exc: object) -> None:
        imgui.destroy_context(self._context)

    def frame(self, count: int = 1) -> None:
        for _ in range(count):
            self.at.clear()
            self.window = None
            imgui.new_frame()
            self.state.frame_snap = self.store.snapshot()
            self.state.frame_now = NOW
            self.app._draw_card_overlays(self.state)
            imgui.end_frame()

    def click(self, label: str) -> None:
        self.io.add_mouse_pos_event(*self.at[label])
        self.frame(2)
        self.io.add_mouse_button_event(0, True)
        self.frame()
        self.io.add_mouse_button_event(0, False)
        self.frame(2)

    def key(self, key: imgui.Key) -> None:
        self.io.add_key_event(key, True)
        self.frame()
        self.io.add_key_event(key, False)
        self.frame()

    @property
    def open(self) -> bool:
        return self.window is not None


@pytest.fixture
def modal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[_Modal]:
    """Details open on a session in a real directory, with its board being recorded."""
    monkeypatch.setattr(brief, "legacy_root", lambda: tmp_path / "projects")
    work = tmp_path / "work"
    work.mkdir()
    with _Modal(monkeypatch, _store(cwd=str(work), brief_dir=str(tmp_path / "brief"))) as real:
        real.state.board_writer = MagicMock()
        real.state.board_logs["s1"] = board_pane.LogState(directory=str(tmp_path / "logs" / "s1"))
        real.work = work  # type: ignore[attr-defined]
        real.state.cards.details.ask(ROOT)
        # Four: the body is a child that fits its rows a frame after the window
        # appears, and the window is moved onto the display the frame after that.
        real.frame(4)
        yield real


def test_the_modal_opens_for_the_node_it_was_asked_for(modal: _Modal) -> None:
    assert modal.open
    assert modal.io.want_capture_keyboard, "a modal claims the keyboard, as the launcher's does"
    assert modal.clipboard == []


@pytest.mark.parametrize("label", ["directory", "writes measured from"])
def test_copy_on_the_directory_puts_exactly_the_path_on_the_clipboard(
    modal: _Modal, label: str
) -> None:
    modal.click(label)
    assert modal.clipboard == [str(modal.work)]  # type: ignore[attr-defined]


def test_copy_on_each_path_puts_that_path_and_nothing_around_it(
    modal: _Modal, tmp_path: Path
) -> None:
    for label in ("transcript", "brief", "board log", "session id"):
        modal.click(label)

    assert modal.clipboard == [
        str(session_details.transcript_for(str(modal.work), "s1")),  # type: ignore[attr-defined]
        str(tmp_path / "brief"),
        str(tmp_path / "logs" / "s1"),
        "s1",
    ]


def test_copy_on_the_task_puts_it_as_it_was_typed(modal: _Modal) -> None:
    modal.click("task")
    assert modal.clipboard == ["rotate the\nsigning key"]


def test_a_row_that_is_not_known_has_no_copy_control(modal: _Modal) -> None:
    for label in ("last active", "contained", "context"):
        assert label not in modal.at
    assert "directory" in modal.at


def test_the_modal_reads_the_board_log_state_the_board_pane_reads(
    modal: _Modal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wiring: ``app.board_logging`` is what the pane is handed, and what this is."""
    handed: list[object] = []
    real = session_details.view

    def seen(snap: object, node: object, **kw: object) -> object:
        handed.append(kw["logging"])
        return real(snap, node, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(session_details, "view", seen)
    modal.frame()

    assert handed == [modal.app.board_logging(modal.state)]
    assert handed[0].sessions["s1"].directory.endswith("logs/s1")  # type: ignore[attr-defined]


def test_escape_closes_the_modal(modal: _Modal) -> None:
    modal.key(imgui.Key.escape)
    modal.frame(2)

    assert not modal.open
    assert modal.state.cards.details.node is None


def test_the_close_button_closes_the_modal(modal: _Modal) -> None:
    modal.click("close")
    assert not modal.open
    assert modal.state.cards.details.node is None


def test_a_closed_modal_reads_the_disk_again_the_next_time_it_is_opened(
    modal: _Modal, monkeypatch: pytest.MonkeyPatch
) -> None:
    looks: list[NodeId] = []
    real = session_details.look
    monkeypatch.setattr(
        session_details, "look", lambda rec, said: (looks.append(rec.node_id), real(rec, said))[1]
    )
    modal.frame(5)
    assert looks == [], "open, it reads nothing"

    modal.key(imgui.Key.escape)
    modal.state.cards.details.ask(BUILDER)
    modal.frame(5)
    assert looks == [BUILDER]
    assert modal.open


def test_the_modal_is_wholly_inside_the_viewport(modal: _Modal) -> None:
    assert modal.window is not None
    x, y, width, height = modal.window
    assert x >= 0.0 and y >= 0.0
    assert x + width <= 1280.0 and y + height <= 900.0


def test_a_modal_left_off_the_display_is_moved_back_by_the_launchers_rule(
    modal: _Modal, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The rule is ``launcher.modal_origin`` and is not written again here. What is
    pinned is that the draw asks it, with this window and the viewport's work
    area, and puts the window where it says.
    """
    asked: list[tuple[object, ...]] = []

    def origin(*args: object) -> tuple[float, float]:
        asked.append(args)
        return (40.0, 30.0)

    monkeypatch.setattr(launcher, "modal_origin", origin)
    before = modal.window
    modal.frame(2)

    assert before is not None and modal.window is not None
    assert asked[0] == ((before[0], before[1]), (before[2], before[3]), (0.0, 0.0), (1280.0, 900.0))
    assert modal.window[:2] == (40.0, 30.0)


def test_a_session_that_leaves_the_store_takes_its_modal_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _Modal(monkeypatch, _store(cwd=None)) as modal:
        modal.state.cards.details.ask(("nowhere", None))
        modal.frame(2)

        assert not modal.open
        assert modal.state.cards.details.node is None


# -- where pptmstr keeps its own files for the session ---------------------------------


def _record(session_id: str, where: Path) -> None:
    """One board record on disk for ``session_id`` under ``where``."""
    declared = boardlog.Declared(at=1.0, task_id="t0", title="task", declared_by="lead")
    assert isinstance(boardlog.append(where, declared), boardlog.Written)


def test_the_look_says_where_the_sessions_own_files_are_and_whether_the_file_is_there() -> None:
    store = _store()
    looked = session_details.look(_rec(store), None)  # type: ignore[arg-type]
    directory = sessionfile.session_dir("s1")
    assert directory is not None

    assert looked.files_read
    assert looked.session_dir == str(directory)
    assert (
        looked.session_file == str(directory / "session.json") == str(sessionfile.file_path("s1"))
    )
    assert looked.board_dir == str(directory / "board") == str(sessionfile.board_dir("s1"))
    assert not looked.session_file_there
    assert (looked.board_legacy, looked.board_split) == (None, False)

    written = sessionfile.update("s1", {"task": "t", "model": "m", "cwd": "/x"})
    assert isinstance(written, sessionfile.Written), written
    assert session_details.look(_rec(store), None).session_file_there  # type: ignore[arg-type]


def test_a_sub_agent_is_not_looked_for_among_the_sessions_files() -> None:
    looked = session_details.look(_rec(_store(), BUILDER), None)  # type: ignore[arg-type]
    assert not looked.files_read and looked.session_dir is None


def test_an_id_that_names_no_directory_has_no_files_and_the_rows_say_that() -> None:
    looked = session_details._session_files("../elsewhere")
    assert looked == Looked(files_read=True)

    shown = _see(_store(), looked=looked)
    for label in ("session directory", "session file"):
        row = _row(shown, label)
        assert (row.value, row.known, row.copy) == (NOT_KNOWN, False, None)
        assert "not one directory name" in row.note
    assert not _row(shown, "board log").known


@pytest.mark.parametrize("own", [False, True])
@pytest.mark.parametrize("legacy", ["none", "empty", "records", "link"])
def test_the_legacy_board_log_is_named_exactly_when_the_board_logs_own_reader_finds_one(
    own: bool, legacy: str
) -> None:
    """
    Held to ``boardsync.find``, which reads the log where the look only lists
    it: the second directory it reports, or the legacy one it read from.
    """
    board = sessionfile.board_dir("s1")
    old = boardlog.session_dir(boardsync.legacy_root(), "s1")
    assert board is not None and old is not None
    if own:
        _record("s1", board)
    if legacy == "empty":
        old.mkdir(parents=True)
    elif legacy == "records":
        _record("s1", old)
    elif legacy == "link":
        board.mkdir(parents=True, exist_ok=True)
        old.parent.mkdir(parents=True)
        old.symlink_to(board, target_is_directory=True)

    found = boardsync.find("s1")
    elsewhere = found.second if found.second is not None else found.directory
    want = None if elsewhere == board else str(elsewhere)
    looked = session_details.look(_rec(_store()), None)  # type: ignore[arg-type]

    assert want == (str(old) if legacy == "records" else None), "the fixture is what it says"
    assert looked.board_legacy == want
    assert looked.board_split is (found.second is not None)
    assert looked.board_dir == str(board)


def test_the_session_file_rows_are_paths_that_copy_as_the_path() -> None:
    looked = Looked(
        files_read=True,
        session_dir="/cfg/sessions/s1",
        session_file="/cfg/sessions/s1/session.json",
        session_file_there=True,
        board_dir="/cfg/sessions/s1/board",
    )
    shown = _see(_store(), looked=looked)

    assert _row(shown, "session directory") == Row(
        "session directory", "/cfg/sessions/s1", copy="/cfg/sessions/s1"
    )
    assert _row(shown, "session file") == Row(
        "session file", "/cfg/sessions/s1/session.json", copy="/cfg/sessions/s1/session.json"
    )
    absent = _see(_store(), looked=dataclasses.replace(looked, session_file_there=False))
    assert _row(absent, "session file").note == "no file is there"
    assert _row(absent, "session file").copy == "/cfg/sessions/s1/session.json"


def test_the_board_log_is_the_one_this_window_opened_and_otherwise_where_one_goes() -> None:
    looked = Looked(files_read=True, board_dir="/cfg/sessions/s1/board")
    opened = board_pane.Logging(sessions={"s1": board_pane.LogState(directory="/logs/s1")})

    assert _row(_see(_store(), looked=looked, logging=opened), "board log") == Row(
        "board log", "/logs/s1", copy="/logs/s1"
    )
    for logging in (None, board_pane.Logging(), board_pane.Logging(off="switched off")):
        row = _row(_see(_store(), looked=looked, logging=logging), "board log")
        assert (row.value, row.copy, row.known) == (
            "/cfg/sessions/s1/board",
            "/cfg/sessions/s1/board",
            True,
        )
        assert "opened no log" in row.note, "a path, and not a claim that a log is there"
    assert _row(_see(_store(), looked=looked), "board recording").known is False


@pytest.mark.parametrize(
    ("split", "said"), [(False, "never appended to"), (True, "second history")]
)
def test_a_legacy_board_log_is_a_row_of_its_own_with_one_line_saying_what_it_is(
    split: bool, said: str
) -> None:
    looked = Looked(
        files_read=True,
        board_dir="/cfg/sessions/s1/board",
        board_legacy="/cfg/boards/s1",
        board_split=split,
    )
    row = _row(_see(_store(), looked=looked), "legacy board log")

    assert (row.value, row.copy, row.known) == ("/cfg/boards/s1", "/cfg/boards/s1", True)
    assert said in row.note and "\n" not in row.note
    assert (
        _see(_store(), looked=dataclasses.replace(looked, board_legacy=None)).row(
            "legacy board log"
        )
        is None
    )


# -- last active, when the caller holds it ---------------------------------------------


def test_last_active_is_said_when_the_caller_holds_it_and_is_a_wall_time() -> None:
    import time

    shown = _see(_store(), last_active=WALL - 75.0)
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(WALL - 75.0))

    assert _row(shown, "last active") == Row("last active", f"{when} (1m15s ago)")
    assert _row(_see(_store()), "last active").known is False


# -- a session recalled from its file --------------------------------------------------

CREATED = WALL - 86_400.0
ACTIVE = WALL - 3 * 3600.0
_FULL = Recalled(
    transcript=TranscriptPresence.PRESENT,
    created_at=CREATED,
    last_active=ACTIVE,
    ended_how=EndedHow.CLOSED,
    policy=Policy.PERMISSIVE,
    contained=True,
    cost_usd=4.25,
)


def _closed(recalled: Recalled = _FULL, *, brief_dir: str | None = "/x/briefs/s1") -> Store:
    store = Store()
    store.apply(
        SessionRecalled(
            session_id="s1",
            task="rotate the signing key",
            model="claude-opus-5",
            recalled=recalled,
            cwd="/x/keys",
            session_base="/x/keys",
            template="team",
            brief=brief_dir,
        ),
        now=NOW,
    )
    return store


def _stamp(at: float) -> str:
    import time

    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(at))


def test_a_closed_session_shows_what_its_file_recorded_and_says_that_is_the_source() -> None:
    shown = _see(_closed())

    assert _row(shown, "state").value == "closed"
    assert _row(shown, "gate").value == "permissive"
    assert "session file last recorded" in _row(shown, "gate").note
    assert _row(shown, "contained").value == "yes"
    assert "session file" in _row(shown, "contained").note
    assert _row(shown, "started").value == f"{_stamp(CREATED)} (24h00m ago)"
    assert _row(shown, "last active").value == f"{_stamp(ACTIVE)} (3h00m ago)"
    assert _row(shown, "ended").value == "closed"
    assert _row(shown, "cost").value == "$4.25"
    assert "estimate" in _row(shown, "cost").note
    assert _row(shown, "directory").copy == "/x/keys"
    assert _row(shown, "team template").value == "team"
    assert shown.row("in this state since") is None
    assert shown.row("error") is None


def test_nothing_a_closed_session_was_not_measured_for_is_drawn_as_a_measurement() -> None:
    """
    The record under it is the field defaults: done, strict, zero spend, no
    sub-agents, an end at no time. Each would read as a fact.
    """
    shown = _see(_closed(Recalled()), held=None)
    rec = _rec(_closed(Recalled()))
    assert (rec.usage.total_cost_usd, rec.policy, rec.state) == (  # type: ignore[attr-defined]
        0.0,
        Policy.STRICT,
        AgentState.DONE,
    )

    for label in (
        "gate",
        "contained",
        "started",
        "last active",
        "ended",
        "cost",
        "tokens",
        "context",
        "sub-agents",
    ):
        row = _row(shown, label)
        assert (row.value, row.known, row.copy) == (NOT_KNOWN, False, None), label
        assert row.note, label
    drawn = [f"{row.value} {row.note}" for section in shown.sections for row in section.rows]
    for lie in ("$0.00", "0 in", "0 out", "strict", "done", "1970"):
        assert not [line for line in drawn if lie in line], lie
    assert _row(shown, "sub-agents").value != "none"
    assert "not run in this window" in _row(shown, "tokens").note
    assert "not run in this window" in _row(shown, "context").note
    assert "not loaded" in _row(shown, "sub-agents").note


def test_a_closed_sessions_values_are_never_the_last_session_objects() -> None:
    """A session object left over under the id says nothing about a recalled card."""
    held = Held(policy=Policy.AUTONOMOUS, contained=False, transcript=None, running=True)
    shown = _see(_closed(), held=held)

    assert _row(shown, "gate").value == "permissive"
    assert _row(shown, "contained").value == "yes"


@pytest.mark.parametrize(
    ("how", "said"),
    [(EndedHow.CLOSED, "closed"), (EndedHow.FAILED, "failed"), (EndedHow.SHUTDOWN, "at shutdown")],
)
def test_how_a_closed_sessions_last_run_ended_is_the_files_word(how: EndedHow, said: str) -> None:
    row = _row(_see(_closed(dataclasses.replace(_FULL, ended_how=how))), "ended")
    assert (row.value, row.known) == (said, True)
    assert "session file" in row.note


def test_a_file_that_records_no_end_is_not_said_to_have_been_closed() -> None:
    row = _row(_see(_closed(dataclasses.replace(_FULL, ended_how=None))), "ended")

    assert (row.value, row.known) == (NOT_KNOWN, False)
    assert "killed or crashed" in row.note and "does not say" in row.note


def test_a_closed_session_that_failed_shows_the_error_its_file_kept() -> None:
    failed = dataclasses.replace(_FULL, ended_how=EndedHow.FAILED, error="the CLI\nexited 1")
    row = _row(_see(_closed(failed)), "error")
    assert (row.value, row.copy) == ("the CLI exited 1", "the CLI\nexited 1")

    silent = dataclasses.replace(_FULL, ended_how=EndedHow.FAILED)
    assert _row(_see(_closed(silent)), "error").known is False
    clean = dataclasses.replace(_FULL, ended_how=EndedHow.CLOSED, error=None)
    assert _see(_closed(clean)).row("error") is None, "an error row is for a run that failed"
    assert _row(_see(_closed(clean)), "ended").value == "closed"


def test_a_session_that_failed_and_was_then_closed_shows_both() -> None:
    """
    Closing a failed card rewrites how its end is described and keeps what the
    failure said. Read as an ordinary close, Details would say "closed" over a
    session that stopped because its CLI died, with the reason in a file and
    nowhere on screen.
    """
    closed = dataclasses.replace(_FULL, ended_how=EndedHow.CLOSED, error="the CLI\nexited 1")
    assert session_details.failed_then_closed(closed)
    seen = _see(_closed(closed))
    assert _row(seen, "ended").value == "closed, after it had failed"
    row = _row(seen, "error")
    assert (row.value, row.copy) == ("the CLI exited 1", "the CLI\nexited 1")

    for how in (EndedHow.FAILED, EndedHow.SHUTDOWN, None):
        other = dataclasses.replace(_FULL, ended_how=how, error="x")
        assert not session_details.failed_then_closed(other)
    assert not session_details.failed_then_closed(
        dataclasses.replace(_FULL, ended_how=EndedHow.CLOSED, error="")
    )


@pytest.mark.parametrize("contained", [True, False])
def test_whether_a_closed_session_was_contained_is_its_files_answer(contained: bool) -> None:
    row = _row(_see(_closed(dataclasses.replace(_FULL, contained=contained))), "contained")
    assert (row.value, row.known) == ("yes" if contained else "no", True)


def test_a_closed_session_whose_transcript_is_gone_says_gone_and_offers_no_path_to_copy() -> None:
    gone = dataclasses.replace(_FULL, transcript=TranscriptPresence.GONE)
    looked = Looked(git_read=True, transcript="/p/s1.jsonl", transcript_there=False)
    row = _row(_see(_closed(gone), looked=looked), "transcript")

    assert (row.value, row.copy) == ("gone", None)
    assert "/p/s1.jsonl" in row.note and "no longer lists" in row.note
    assert _row(_see(_closed(gone)), "transcript").value == "gone"


@pytest.mark.parametrize("presence", [TranscriptPresence.PRESENT, TranscriptPresence.UNKNOWN])
def test_a_closed_session_whose_transcript_is_not_gone_shows_where_the_naming_puts_it(
    presence: TranscriptPresence,
) -> None:
    there = dataclasses.replace(_FULL, transcript=presence)
    looked = Looked(git_read=True, transcript="/p/s1.jsonl", transcript_there=True)
    row = _row(_see(_closed(there), looked=looked), "transcript")

    assert (row.value, row.copy) == ("/p/s1.jsonl", "/p/s1.jsonl")
    assert "naming" in row.note


def test_a_closed_session_with_no_brief_on_file_is_not_said_to_have_been_launched_bare() -> None:
    row = _row(_see(_closed(brief_dir=None)), "brief")
    assert (row.value, row.known) == (NOT_KNOWN, False)
    assert _row(_see(_closed()), "brief").copy == "/x/briefs/s1"


def test_last_active_handed_in_for_a_closed_session_does_not_replace_its_files() -> None:
    shown = _see(_closed(), last_active=WALL - 1.0)
    assert _row(shown, "last active").value == f"{_stamp(ACTIVE)} (3h00m ago)"


# -- the new rows, in frames -----------------------------------------------------------


def test_copy_on_each_of_the_sessions_own_paths_puts_that_path_on_the_clipboard(
    modal: _Modal,
) -> None:
    directory = sessionfile.session_dir("s1")
    assert directory is not None
    for label in ("session directory", "session file"):
        modal.click(label)

    assert modal.clipboard == [str(directory), str(directory / "session.json")]


def test_the_close_button_is_on_the_display_when_the_rows_are_taller_than_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    A closed session's rows are taller than a 900-pixel display. They scroll in
    the body; the close button under them stays inside the window and on the
    display, and it closes.
    """
    monkeypatch.setattr(brief, "legacy_root", lambda: tmp_path / "projects")
    old = boardlog.session_dir(boardsync.legacy_root(), "s1")
    assert old is not None
    _record("s1", old)
    with _Modal(monkeypatch, _closed()) as modal:
        modal.state.cards.details.ask(ROOT)
        modal.frame(4)
        assert modal.window is not None
        x, y, width, height = modal.window
        assert y + height <= 900.0
        assert y < modal.at["close"][1] < y + height
        (body,) = [
            window
            for window in imgui.get_current_context().windows
            if window.name.startswith(f"{session_details.TITLE}/{session_details.BODY_ID}")
        ]
        assert body.scroll_max.y > 0.0, "the rows scroll: this is the case under test"
        modal.click("close")
        assert not modal.open


def test_the_modal_for_a_closed_session_reads_its_files_once_and_copies_its_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(brief, "legacy_root", lambda: tmp_path / "projects")
    old = boardlog.session_dir(boardsync.legacy_root(), "s1")
    assert old is not None
    _record("s1", old)
    with _Modal(monkeypatch, _closed()) as modal:
        modal.state.cards.details.ask(ROOT)
        modal.frame(4)
        assert modal.open
        modal.click("gate")
        # The logs are the last rows of a body taller than the display: scrolled to,
        # with the wheel over the body, as an operator reaches them.
        modal.io.add_mouse_pos_event(*modal.at["gate"])
        modal.io.add_mouse_wheel_event(0.0, -50.0)
        modal.frame(3)
        for label in ("legacy board log", "board log"):
            modal.click(label)
        for label in ("cost", "tokens", "context", "sub-agents", "contained"):
            assert label not in modal.at

    assert modal.clipboard == ["permissive", str(old), str(sessionfile.board_dir("s1"))]


def test_a_session_older_than_its_file_says_first_seen_and_not_started() -> None:
    """
    F3. A file begun by a resume has ``created_at`` from that resume. Drawn as
    "started", it would date a session that may be months old to last week.
    """
    older = dataclasses.replace(_FULL, task_recorded=False)
    shown = _see(_closed(older))

    assert shown.row("started") is None
    seen = _row(shown, "first seen")
    assert seen.value == f"{_stamp(CREATED)} (24h00m ago)"
    assert "began earlier" in seen.note and "session file" in seen.note
    for recorded in (True, None):
        ordinary = _see(_closed(dataclasses.replace(_FULL, task_recorded=recorded)))
        assert ordinary.row("first seen") is None and _row(ordinary, "started")


def test_a_closed_sessions_cost_says_it_may_count_a_resume_twice() -> None:
    """
    The file's figure adds each run's reading to what the file held, which is
    right only if a resumed CLI reports from zero. That is not established.
    """
    note = _row(_see(_closed()), "cost").note
    assert "estimate" in note and "may double-count across resumes" in note


def test_a_title_that_is_only_a_label_is_not_shown_as_the_sessions_task() -> None:
    """
    A session whose original task nothing recorded, as a closed card and as the
    record a resume of it announces. "task: <the CLI's summary>" would state as
    the session's instruction a line nobody gave it, on the one surface that is
    asked what a session's parameters are.
    """
    closed = _see(_closed(dataclasses.replace(_FULL, task_recorded=False)))
    assert closed.row("task") is None
    row = _row(closed, "title")
    assert (row.value, row.note) == ("rotate the signing key", session_details.TITLE_IS_LABEL)

    resumed = Store()
    resumed.apply(
        AgentSpawned(
            node_id=ROOT,
            parent=None,
            task="rotate the signing key",
            model="claude-opus-5",
            started_at=1.0,
            task_is_label=True,
        ),
        now=1.0,
    )
    seen = _see(resumed)
    assert seen.row("task") is None
    assert _row(seen, "title").note == session_details.TITLE_IS_LABEL

    # A recorded task is a task, with nothing said under it.
    for store in (_store(), _closed(dataclasses.replace(_FULL, task_recorded=True)), _closed()):
        plain = _see(store)
        assert plain.row("title") is None and _row(plain, "task").note == ""


# -- the board's routine provenance lives here (U8) ------------------------------------


def _restored_view(restore: object, logging: board_pane.Logging | None) -> View:
    snap = dataclasses.replace(
        _store().snapshot(), restores={"s1": restore}  # type: ignore[dict-item]
    )
    shown = session_details.view(
        snap, ROOT, held=None, looked=None, logging=logging, now=NOW, wall_now=WALL
    )
    assert shown is not None
    return shown


@pytest.mark.parametrize("short", [False, True])
def test_the_restore_row_is_the_board_panes_own_line_routine_or_alarm(short: bool) -> None:
    """
    The board header no longer draws a whole restore; this row is where it is read,
    in the words ``restore_line`` gives it -- and an alarm, which the header still
    draws, is here as well.
    """
    from pptmstr.model import Restore

    restore = Restore(session_id="s1", at=5.0, tasks=2, concerns=1, log_short=short)
    entry = board_pane.LogState(directory="/logs/s1", newest=WALL - 60.0)
    logging = board_pane.Logging(sessions={"s1": entry})

    shown = _restored_view(restore, logging)
    line = board_pane.restore_line(restore, entry)

    assert line is not None
    assert line.tone is (board_pane.Tone.ALARM if short else board_pane.Tone.PROSE)
    assert _row(shown, "board restore") == Row("board restore", line.text)
    recorded = board_pane.recording_line(None, entry)
    assert recorded is not None
    assert _row(shown, "board recording").value == recorded.text


def test_a_launched_session_nothing_was_restored_for_has_no_restore_row() -> None:
    assert _see(_store()).row("board restore") is None


def test_a_restore_still_being_applied_claims_no_answer() -> None:
    logging = board_pane.Logging(
        sessions={"s1": board_pane.LogState(directory="/logs/s1", recording=False, awaiting=True)}
    )
    row = _row(_see(_store(), logging=logging), "board restore")
    assert (row.value, row.known) == (NOT_KNOWN, False)
    assert "restore" in row.note


def test_a_closed_session_does_not_say_its_board_was_never_restored() -> None:
    row = _row(_see(_closed()), "board restore")
    assert (row.value, row.known) == (NOT_KNOWN, False)
    assert "resumed" in row.note
