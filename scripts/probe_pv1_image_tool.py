#!/usr/bin/env python3
"""
Probe PV1: does the CLI hand an image returned by an in-process MCP tool back to
the model, and what does pptmstr's driver get back for it?

    .venv/bin/python scripts/probe_pv1_image_tool.py [--live] [--out DIR]

The tool is built the way ``bus.build_server`` builds pptmstr's: ``@tool`` handlers
in a ``create_sdk_mcp_server`` named ``pptmstr``, passed as ``mcp_servers`` with
pptmstr's session shape (``permission_mode="dontAsk"``, ``strict_mcp_config=True``,
and a ``PreToolUse`` gate that allows only the probe's tool).

Without ``--live`` nothing reaches the CLI: the PNG is built, the handler is called
in-process and its result put through the SDK's own ``_convert_tool_content``, which
is what the SDK hands the CLI. That is the half the sandbox can run. ``--live`` runs
two real sessions on ``haiku``:

- ``control``: the tool returns text only, so the model has no image to read.
- ``image``: the tool returns the PNG (a red square with a white digit 7) plus a
  one-line text block that names neither colour nor digit.

Each records the model's answer, the ``ToolResultBlock.content`` the driver would
see (base64 elided to its length and head), what ``transcript.cap_output`` and
``tool_result_line`` make of that content today, and the per-message and per-session
token usage. ``results.json`` is written under ``--out``; a summary goes to stdout.
The write-up is notes/vector-rendering/probe-pv1.md.

Nothing in ``pptmstr/`` is written.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import struct
import sys
import zlib
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import claude_agent_sdk  # noqa: E402
from claude_agent_sdk import (  # noqa: E402
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)

from pptmstr.bus import SERVER_NAME  # noqa: E402
from pptmstr.transcript import cap_output, tool_result_line  # noqa: E402

MODEL = "haiku"
TOOL = "show_figure"
QUALIFIED = f"mcp__{SERVER_NAME}__{TOOL}"
# CLI 2.1.301 defers MCP tools behind ToolSearch, so the model has to load the
# schema first. pptmstr's gate auto-approves ToolSearch (approval._AUTO); so must
# this one, or the tool is unreachable.
ALLOWED = frozenset({QUALIFIED, "ToolSearch"})
SIZE = 64

PROMPT = (
    f"Call the {TOOL} tool exactly once with no arguments. Then answer on one line, "
    "in the form 'COLOUR=<background colour> DIGIT=<digit>', what the figure it "
    "returned shows. If you received no image, answer 'COLOUR=none DIGIT=none'. "
    "Do not call any other tool."
)


# -- the image -------------------------------------------------------------------


def _seven(x: int, y: int) -> bool:
    """True where the white glyph is: a top bar and a diagonal stroke down-left."""
    if 12 <= y < 20 and 16 <= x < 48:
        return True
    if 20 <= y < 54:
        # The stroke runs from (47, 20) to (26, 53), eight pixels wide.
        centre = 47 - (y - 20) * 21 / 33
        return centre - 7 <= x <= centre
    return False


def red_seven_png() -> bytes:
    """A 64x64 RGB PNG: red background, white 7. Stdlib only, byte-for-byte stable."""
    rows = bytearray()
    for y in range(SIZE):
        rows.append(0)  # filter: none
        for x in range(SIZE):
            rows += b"\xff\xff\xff" if _seven(x, y) else b"\xdc\x14\x14"

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )


PNG = red_seven_png()
PNG_B64 = base64.b64encode(PNG).decode("ascii")


def build_server(with_image: bool) -> Any:
    @tool(TOOL, "Return the figure pptmstr has prepared for this turn.", {})
    async def show_figure(args: dict[str, Any]) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{"type": "text", "text": "Figure attached."}]
        if with_image:
            content.append({"type": "image", "data": PNG_B64, "mimeType": "image/png"})
        else:
            content = [{"type": "text", "text": "No figure is attached to this result."}]
        return {"content": content}

    return create_sdk_mcp_server(SERVER_NAME, "1.0.0", [show_figure]), show_figure


# -- shape helpers ---------------------------------------------------------------


def elide(value: Any) -> Any:
    """``value`` with every long base64-looking string replaced by its length and head."""
    if isinstance(value, dict):
        return {k: elide(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [elide(v) for v in value]
    if isinstance(value, str) and len(value) > 120:
        return f"<str len={len(value)} head={value[:32]!r} is_png_b64={value == PNG_B64}>"
    if is_dataclass(value) and not isinstance(value, type):
        return elide(asdict(value))
    return value


def rendered(content: object, name: str = QUALIFIED) -> dict[str, Any]:
    """What pptmstr's transcript makes of a ToolResultBlock.content today."""
    output, dropped = cap_output(content)
    line = tool_result_line(name, output)
    return {
        "content_python_type": type(content).__name__,
        "cap_output_len": len(output),
        "cap_output_dropped": dropped,
        "cap_output_contains_base64": PNG_B64 in output,
        "cap_output_head": output[:200],
        "tool_result_line_len": len(line),
        "tool_result_line": line[:400],
    }


def offline() -> dict[str, Any]:
    from claude_agent_sdk import _convert_tool_content  # type: ignore[attr-defined]

    server, handler = build_server(with_image=True)
    raw = asyncio.run(handler.handler({}))
    converted = _convert_tool_content(raw["content"])
    return {
        "png_bytes": len(PNG),
        "png_b64_chars": len(PNG_B64),
        "server_config_keys": sorted(server.keys()),
        "handler_result": elide(raw),
        "sdk_converted_for_cli": elide(converted),
        # Not what the CLI returns -- the live run says that. This is what the
        # transcript would render if the CLI echoed the MCP blocks unchanged.
        "if_cli_echoed_mcp_blocks": rendered(converted),
    }


# -- live ------------------------------------------------------------------------


async def run(name: str, with_image: bool, cwd: Path) -> dict[str, Any]:
    record: dict[str, Any] = {"scenario": name, "with_image": with_image}
    calls: list[dict[str, Any]] = []
    texts: list[str] = []
    usages: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    names: dict[str, str] = {}

    async def gate(input_data: Any, tool_use_id: Any, context: Any) -> dict[str, Any]:
        name_ = input_data.get("tool_name")
        calls.append({"tool": name_, "input": input_data.get("tool_input")})
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow" if name_ in ALLOWED else "deny",
                "permissionDecisionReason": "probe-pv1",
            }
        }

    server, _ = build_server(with_image)
    options = ClaudeAgentOptions(
        model=MODEL,
        cwd=str(cwd),
        mcp_servers={SERVER_NAME: server},
        permission_mode="dontAsk",
        strict_mcp_config=True,
        allowed_tools=sorted(ALLOWED),
        max_turns=6,
        hooks={"PreToolUse": [HookMatcher(hooks=[gate])]},  # type: ignore[list-item]
        stderr=lambda line: record.setdefault("stderr", []).append(line[:300]),
    )
    client = ClaudeSDKClient(options)
    try:
        await client.connect()
        await client.query(PROMPT)
        async for message in client.receive_response():
            if isinstance(message, SystemMessage) and message.subtype == "init":
                data = message.data
                record["init"] = {
                    "claude_code_version": data.get("claude_code_version"),
                    "model": data.get("model"),
                    "apiKeySource": data.get("apiKeySource"),
                    "mcp_servers": data.get("mcp_servers"),
                    "has_tool": QUALIFIED in (data.get("tools") or []),
                }
            elif isinstance(message, AssistantMessage):
                usages.append({"message_id": message.message_id, "usage": message.usage})
                if message.error:
                    record.setdefault("assistant_errors", []).append(str(message.error))
                for block in message.content:
                    if isinstance(block, TextBlock):
                        texts.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        names[block.id] = block.name
                        record.setdefault("tool_use_blocks", []).append(
                            {"name": block.name, "input": block.input}
                        )
            elif isinstance(message, UserMessage) and not isinstance(message.content, str):
                for block in message.content:
                    if isinstance(block, ToolResultBlock):
                        tool_name = names.get(block.tool_use_id, "tool")
                        results.append(
                            {
                                "tool": tool_name,
                                "is_error": block.is_error,
                                "content_shape": elide(block.content),
                                "tool_use_result": elide(message.tool_use_result),
                                "transcript": rendered(block.content, tool_name),
                            }
                        )
            elif isinstance(message, ResultMessage):
                record["result"] = {
                    "is_error": message.is_error,
                    "subtype": message.subtype,
                    "num_turns": message.num_turns,
                    "cost_usd": message.total_cost_usd,
                    "usage": message.usage,
                    "model_usage": elide(message.model_usage),
                    "result": (message.result or "")[:400],
                }
    except Exception as exc:  # noqa: BLE001 -- recorded, the next scenario still runs
        record["exception"] = f"{type(exc).__name__}: {exc}"[:600]
    finally:
        try:
            await client.disconnect()
        except Exception as exc:  # noqa: BLE001
            record["disconnect_exception"] = f"{type(exc).__name__}: {exc}"[:300]
    record["gate_calls"] = calls
    record["assistant_usage"] = usages
    record["tool_results"] = results
    record["texts"] = [t[:600] for t in texts]
    return record


def summary(record: dict[str, Any]) -> dict[str, Any]:
    result = record.get("result") or {}
    return {
        "scenario": record["scenario"],
        "answer": (record.get("texts") or ["<none>"])[-1][:200],
        "tool_called": [c["tool"] for c in record.get("gate_calls", [])],
        "result_content_shape": [r["content_shape"] for r in record.get("tool_results", [])],
        "transcript_line_len": [
            r["transcript"]["tool_result_line_len"] for r in record.get("tool_results", [])
        ],
        "transcript_has_base64": [
            r["transcript"]["cap_output_contains_base64"] for r in record.get("tool_results", [])
        ],
        "per_message_input_tokens": [
            {
                k: (u.get("usage") or {}).get(k)
                for k in (
                    "input_tokens",
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                    "output_tokens",
                )
            }
            for u in record.get("assistant_usage", [])
        ],
        "session_usage": result.get("usage"),
        "cost_usd": result.get("cost_usd"),
        "is_error": result.get("is_error"),
        "exception": record.get("exception"),
    }


async def live(cwd: Path) -> list[dict[str, Any]]:
    return [await run("control", False, cwd), await run("image", True, cwd)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--out", default=os.path.join(os.environ.get("TMPDIR", "/tmp"), "probe-pv1")
    )
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / "red-seven.png").write_bytes(PNG)

    report: dict[str, Any] = {
        "sdk_version": claude_agent_sdk.__version__,
        "model": MODEL,
        "offline": offline(),
    }
    if args.live:
        # cwd is the scratch directory so no project CLAUDE.md inflates the counts.
        report["live"] = asyncio.run(live(out))
        report["summary"] = [summary(r) for r in report["live"]]
    (out / "results.json").write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({k: v for k, v in report.items() if k != "live"}, indent=2, default=str))
    print(f"wrote {out / 'results.json'}")


if __name__ == "__main__":
    main()
