# Containment took the operator's MCP tools with it

**Dated:** 2026-09-27 ·
**Status:** built; on branch `model-catalog-ingest` ·
**Origin:** the operator noticing that MCP tools had stopped working in every session ·
**Repairs a consequence of:** `e3a5e7a` — *"A session can run unattended, bounded by the
sandbox it may not start without"* ·
**Does not reverse it:** `strict_mcp_config=True` still stands — see §2

---

## 1. What broke, and why nothing pointed at it

`e3a5e7a` set `strict_mcp_config=True` on `ClaudeAgentOptions`. That flag tells the CLI
to load *only* the servers the SDK passes and to ignore every other source — the
operator's project `.mcp.json`, their user settings, their claude.ai connectors.

At the time the only server pptmstr passed was the in-process bus. So from that commit
onward, **every session had exactly one MCP server and no others**, at every rung
including `STRICT`. Nothing failed loudly. The tools were simply absent, and there was no
inline path to approve one back — the operator could not accept a prompt to enable a
server that was never offered.

The flag is correct and it is load-bearing (§2). The defect is that its consequence was
never written down: `e3a5e7a`'s commit message does not mention MCP, connectors, or
strict config anywhere, and no planning record covered it. A capability regression that
leaves no trace is indistinguishable from the feature never having worked, which is why
this record exists at all rather than only the fix.

**The lesson is about the record, not the flag.** A change that closes a door should name
the door in the same commit that closes it. This one was reviewed on what it added — a
sandbox — and not on what it took away.

## 2. Why `strict_mcp_config=True` stays

It is not a cost to be paid down. Without it the CLI loads whatever MCP configuration it
finds on the machine, and an **stdio** server from one of those sources runs as a child
of the CLI process — which `sandbox.py` records as running *outside* the sandbox. A
containment configuration that can be sidestepped by a file in the working directory is
not a containment configuration.

So the fix could not be "turn strict off". It had to be "name what gets loaded".

## 3. The fix: admission, held in settings

`Settings.admitted_connectors` is a tuple of `McpConnector(name, url, id)` — claude.ai
connectors the operator has admitted into every session. `driver._mcp_servers()` builds
the in-process bus plus one `{"type": "claudeai-proxy", …}` entry per admitted connector,
and that dict is the whole of what a session may reach.

**Why settings rather than per-launch.** Which connectors exist is a property of the
machine the operator is sitting at, not of the piece of work they just typed. Putting the
list on `LaunchSpec` would ask the same question at every launch and get the same answer
every time. This is the same split `app._launch` already draws for `subagent_cap`.

**Why a stored list rather than an inline prompt.** An inline approve-this-server flow is
the thing `strict_mcp_config` exists to prevent: it would let a server the operator never
named reach a session by being asked for at the right moment. Admission is deliberately a
decision made away from the session that benefits from it.

## 4. Admission is not a widening of the gate

Worth stating because the two look alike and are not. An admitted connector's tools
arrive as `mcp__<server>__<name>`, which reaches `approval.classify`'s **fail-closed
fallthrough**. They park in front of the operator at every policy, `AUTONOMOUS` included,
and no rung widens that. What admission changes is whether the tool is *reachable*, not
whether it is *gated*.

## 5. A live SDK gap, recorded so the workaround can be removed

`_mcp_servers()` casts its connector entries to `Any`. `McpClaudeAIProxyServerConfig` is
defined in `claude_agent_sdk.types` and is the shape `get_mcp_status` reports a connector
as — but it is **absent from the `McpServerConfig` union** that annotates `mcp_servers`
as of 0.2.136. The cast is that omission and nothing else. Re-check it on the next SDK
bump; when the union gains the member, the cast comes out.

## 6. Not re-validated at launch

`McpConnector.id` is the account's `mcpsrv_` identifier and is taken on trust. A rotated
id means the CLI does not connect that server, and the operator notices and re-admits.
Checking every id on every launch would put a network fetch per connector in front of
every session to catch a rare case.

## 7. Outstanding

- **`scripts/verify_mcp_status.py` does not exist.** `settings.McpConnector`'s docstring
  tells the operator to run it after a rotated id. Either it is written or the reference
  comes out; today it is a dangling instruction.
- **No live session has been driven with an admitted connector.** The plumbing is covered
  by `tests/test_driver.py`, which is a different claim — see STYLE.md §2. What has not
  been observed is a real claude.ai connector's tools appearing in a real session and
  parking at the gate.
- **No UI.** Admission is a hand-edit of `settings.json`. There is no launcher or
  settings surface for it.
