#!/usr/bin/env python3
"""
Does a real session launched through pptmstr's driver load the shipped figures
skill, and does the gate admit the call without asking?

tests/test_skill_ship.py holds the options and the argv; the probe
(notes/vector-rendering/probe-skills.md) ran the plugin through a bare
``ClaudeSDKClient``. Neither ran ``AgentSession`` against the CLI with the real
``_options``, containment-free, with its own hook. This does, once, solo, with a
drawing prompt and the default STRICT policy.

Every call the gate sees is recorded with its disposition (``driver.classify`` is
wrapped, not replaced). A call that parks is refused at once and recorded, since
nobody is there to answer: a ``Skill`` call that parks is the failure this looks for.

Verdicts:
  PASS      a Skill call named a shipped skill, was AUTO_APPROVE, nothing named
            Skill parked, and the reply carries a ```mermaid fence
  NO-SKILL  the session drew without loading the skill (a behaviour result, n=1)
  PARKED    a Skill call naming a shipped skill reached the operator: the gate is wrong
  FAILED    the session failed or ended without a reply, or the deadline passed

Writes ``--out``/result.json (default shots/skill-ship-live). Needs a login, so it
cannot run in the agent sandbox.

Usage:  .venv/bin/python scripts/verify_skill_ship_live.py [--model haiku] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pptmstr import driver, plugins  # noqa: E402
from pptmstr.approval import Disposition  # noqa: E402
from pptmstr.bridge import Bridge, Decision  # noqa: E402
from pptmstr.driver import AgentSession  # noqa: E402
from pptmstr.model import ApprovalNeeded  # noqa: E402
from pptmstr.store import Store  # noqa: E402
from pptmstr.transcript import SegmentKind  # noqa: E402

PROMPT = (
    "Draw me a dependency graph of three packages: app depends on core and ui, and "
    "ui depends on core. Keep the reply short."
)
DEADLINE_S = 180.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="haiku")
    parser.add_argument("--out", default=str(ROOT / "shots" / "skill-ship-live"))
    parser.add_argument("--deadline", type=float, default=DEADLINE_S)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    calls: list[dict[str, Any]] = []
    real = driver.classify

    def recorded(tool_name: str, tool_input: Any, *rest: Any, **kw: Any) -> Disposition:
        disposition = real(tool_name, tool_input, *rest, **kw)
        calls.append(
            {"tool": tool_name, "input": dict(tool_input), "disposition": disposition.value}
        )
        return disposition

    driver.classify = recorded  # type: ignore[assignment]

    bridge = Bridge()
    bridge.start()
    store = Store()
    session = AgentSession(bridge, task=PROMPT, model=args.model)
    options = session._options()
    session.announce()
    running = bridge.submit(session.run())

    parked: list[str] = []
    answered: set[str] = set()
    started = time.monotonic()
    timed_out = True
    try:
        while time.monotonic() - started < args.deadline:
            for effect in store.apply_all(bridge.drain()):
                bridge.settle(effect)
            for obligation in store.snapshot().needs_you:
                if isinstance(obligation, ApprovalNeeded):
                    pending = obligation.approval
                    if pending.id not in answered:
                        answered.add(pending.id)
                        parked.append(obligation.summary)
                        bridge.resolve(
                            pending.id, Decision(approved=False, reason="no operator: probe")
                        )
            if running.done() or _replied(store, session):
                timed_out = False
                break
            time.sleep(0.05)
    finally:
        session.teardown_requested = True
        for effect in store.apply_all(bridge.drain()):
            bridge.settle(effect)
        bridge.stop()

    reply = _output(session)
    node = store.snapshot().nodes.get(session.node_id)
    final = node.state.value if node is not None else None
    skill_calls = [c for c in calls if c["tool"] == "Skill"]
    shipped = [c for c in skill_calls if c["input"].get("skill") in plugins.SKILLS]
    if any(c["disposition"] != Disposition.AUTO_APPROVE.value for c in shipped):
        verdict = "PARKED"
    elif not reply or final == "failed":
        verdict = "FAILED"
    elif shipped and "```mermaid" in reply:
        verdict = "PASS"
    elif not shipped:
        verdict = "NO-SKILL"
    else:
        verdict = "FAILED"
    result = {
        "verdict": verdict,
        "timed_out": timed_out,
        "final_state": final,
        "model": args.model,
        "plugins_option": options.plugins,
        "skills_option": options.skills,
        "setting_sources_option": options.setting_sources,
        "gate_calls": calls,
        "parked": parked,
        "reply": reply,
        "transcript": session.transcript.read(0, session.transcript.published_length),
    }
    (out / "result.json").write_text(json.dumps(result, indent=2, default=str))
    print(f"{verdict}: {len(skill_calls)} Skill call(s), {len(parked)} parked")
    print(f"wrote {out / 'result.json'}")
    return 0 if verdict == "PASS" else 1


def _output(session: AgentSession) -> str:
    transcript = session.transcript
    return "".join(
        transcript.read(seg.start, seg.end)
        for seg in transcript.segments()
        if seg.kind is SegmentKind.OUTPUT
    )


def _replied(store: Store, session: AgentSession) -> bool:
    """
    The root has ended its turn: waiting for input after writing something, or
    in a terminal state (the sandbox's "Not logged in" ends FAILED).
    """
    from pptmstr.model import AgentState

    node = store.snapshot().nodes.get(session.node_id)
    if node is None:
        return False
    if node.state in (AgentState.DONE, AgentState.FAILED, AgentState.CANCELLED):
        return True
    return node.state is AgentState.AWAITING_INPUT and bool(_output(session))


if __name__ == "__main__":
    sys.exit(main())
