"""
Claim 7: ui/launcher.py's new-session spec decides containment with
``policy is Policy.AUTONOMOUS``, not ``approval.requires_containment``.

The real expressions are executed, not re-typed: the ``engaged = ...`` assignment
and the ``policy=`` keyword of ``LauncherState.spec`` are lifted out of the source
by AST and evaluated against a stub ``self``; ``dangerous_engaged`` and
``granted_policy`` are called through their property getters on a stub. Every
``Policy`` member is tried, plus a synthetic future rung ``FUTURE`` for which
``requires_containment`` is patched to return True (and which is not AUTONOMOUS).

Then what happens downstream to a spec with a rung that needs containment and no
containment: ``driver.AgentSession._uncontained_autonomy`` (called by ``run``
before the client starts, raising ``UncontainedAutonomy``) is called on a stub.
"""

from __future__ import annotations

import ast
import types

import _common  # noqa: F401
from _common import find_def, source_of, verdict

from pptmstr import approval, driver
from pptmstr.approval import Policy
from pptmstr.ui import launcher

SRC = source_of("pptmstr/ui/launcher.py")
spec_fn = find_def(ast.parse(SRC), "spec", cls="LauncherState")
engaged_node = next(
    n for n in ast.walk(spec_fn)
    if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "engaged"
)
policy_kw = next(
    k for n in ast.walk(spec_fn) if isinstance(n, ast.Call)
    for k in n.keywords if k.arg == "policy"
)
containment_kw = next(
    k for n in ast.walk(spec_fn) if isinstance(n, ast.Call)
    for k in n.keywords if k.arg == "containment"
)
print(f"LauncherState.spec at :{spec_fn.lineno}")
print(f"  :{engaged_node.lineno} engaged = {ast.unparse(engaged_node.value)}")
print(f"  :{containment_kw.value.lineno} containment = {ast.unparse(containment_kw.value)}")
print(f"  :{policy_kw.value.lineno} policy = {ast.unparse(policy_kw.value)}")
engaged_code = compile(ast.Expression(engaged_node.value), "<engaged>", "eval")
policy_code = compile(ast.Expression(policy_kw.value), "<policy>", "eval")

FUTURE = types.SimpleNamespace(value="future", name="FUTURE")  # not a Policy member
real_requires = approval.requires_containment


def requires(p: object) -> bool:
    return True if p is FUTURE else real_requires(p)  # type: ignore[arg-type]


def launcher_eval(policy: object, refused: bool) -> tuple[bool, object]:
    stub = types.SimpleNamespace(policy=policy, _read_refusal=lambda d, c: "refused" if refused else None)
    env = {"self": stub, "Policy": Policy, "directory": "/d", "resolved_cwd": "/d"}
    engaged = eval(engaged_code, env)
    env["engaged"] = engaged
    return engaged, eval(policy_code, env)


def props(policy: object) -> tuple[bool, object]:
    stub = types.SimpleNamespace(policy=policy, containment_refusal=lambda: None,
                                 picker=types.SimpleNamespace(picked=None))
    de = launcher.LauncherState.dangerous_engaged.fget(stub)  # type: ignore[attr-defined]
    stub.dangerous_engaged = de
    gp = launcher.LauncherState.granted_policy.fget(stub)  # type: ignore[attr-defined]
    return de, gp


def driver_refusal(policy: object, contained: bool) -> str | None:
    stub = types.SimpleNamespace(policy=policy, contained=contained)
    stub._unpaired = lambda p: driver.AgentSession._unpaired(stub, p)  # type: ignore[arg-type]
    driver.requires_containment = requires  # type: ignore[assignment]
    try:
        return driver.AgentSession._uncontained_autonomy(stub)  # type: ignore[arg-type]
    finally:
        driver.requires_containment = real_requires  # type: ignore[assignment]


def name(p: object) -> str:
    return getattr(p, "name", str(p))


print()
hdr = (f"{'rung':<12} {'requires_containment':<21} {'spec engaged':<13} {'spec policy':<12} "
       f"{'containment?':<13} {'dangerous_engaged':<18} {'granted_policy':<15} match  driver start")
print(hdr)
mismatches = []
for p in [*Policy, FUTURE]:
    rc = requires(p)
    eng, pol = launcher_eval(p, refused=False)
    de, gp = props(p)
    start = driver_refusal(pol, contained=eng)
    start_s = "refused (UncontainedAutonomy)" if start else "starts"
    match = rc == eng
    if not match:
        mismatches.append(name(p))
    print(f"{name(p):<12} {str(rc):<21} {str(eng):<13} {name(pol):<12} {str(eng):<13} "
          f"{str(de):<18} {name(gp):<15} {str(match):<6} {start_s}")

print()
print("with the directory refused (_read_refusal != None):")
for p in [*Policy, FUTURE]:
    eng, pol = launcher_eval(p, refused=True)
    print(f"  {name(p):<12} engaged={eng} policy={name(pol)}")

print()
real_mismatch = [m for m in mismatches if m != "FUTURE"]
if real_mismatch:
    verdict("CONFIRMED (live)", f"mismatch on existing rungs {real_mismatch}")
elif mismatches == ["FUTURE"]:
    verdict(
        "CONFIRMED as latent",
        "for every existing Policy member the launcher's by-name test agrees with "
        "requires_containment (only AUTONOMOUS needs it); a future rung that needs "
        "containment gets engaged=False -> containment=None with its policy kept. "
        "The plan's stated consequence is wrong: such a spec does not launch "
        "uncontained -- driver.AgentSession.run refuses it at start "
        "(UncontainedAutonomy via _uncontained_autonomy), as the last column shows. "
        "dangerous_engaged and granted_policy also test by name (launcher :2816, :2835).",
    )
else:
    verdict("REFUTED", "the launcher agrees with requires_containment for every rung, synthetic included")
