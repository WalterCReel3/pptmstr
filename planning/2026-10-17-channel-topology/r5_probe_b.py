#!/usr/bin/env python3
"""
S1 needs-a-run item B: what a second ClaudeSDKClient.query() does when it is
written while the first turn is still running (pptmstr's AgentSession.wake calls
query() with no check that the session is idle).

Turn 1 asks for a 25 s Bash sleep. 6 s after turn 1 starts, query() is called
again. Every message is printed with seconds since start, so the output shows
whether the second prompt interrupted turn 1, was folded into it, or was taken
as a turn of its own after turn 1's result.

Needs the claude CLI logged in and network to the API, so it is run by the
operator, not from the sandbox.
"""

import asyncio
import time

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

T0 = time.monotonic()


def t() -> str:
    return f"{time.monotonic() - T0:6.1f}s"


async def main() -> None:
    options = ClaudeAgentOptions(
        model="claude-haiku-4-5",
        allowed_tools=["Bash"],
        permission_mode="bypassPermissions",
        max_turns=6,
    )
    async with ClaudeSDKClient(options=options) as client:
        await client.query(
            "Run exactly this with the Bash tool: sleep 25 && echo SLEPT."
            " Then reply with the single word FIRST."
        )
        print(t(), "query 1 written")

        async def second() -> None:
            await asyncio.sleep(6)
            await client.query("Reply with the single word SECOND.")
            print(t(), "query 2 written (mid-turn)")

        task = asyncio.create_task(second())
        results = 0
        async for msg in client.receive_messages():
            if isinstance(msg, AssistantMessage):
                for b in msg.content:
                    if isinstance(b, TextBlock):
                        print(t(), "assistant text:", b.text.strip()[:80])
                    elif isinstance(b, ToolUseBlock):
                        print(t(), "tool use:", b.name, str(b.input)[:60])
            elif isinstance(msg, ResultMessage):
                results += 1
                print(
                    t(),
                    f"RESULT #{results}: subtype={msg.subtype} is_error={msg.is_error}"
                    f" num_turns={msg.num_turns}",
                )
                if results >= 2:
                    break
            else:
                print(t(), type(msg).__name__)
            if time.monotonic() - T0 > 120:
                print(t(), "timeout")
                break
        await task


async def bounded() -> None:
    try:
        await asyncio.wait_for(main(), timeout=150)
    except TimeoutError:
        print(t(), "no second result within 150 s: the second prompt was folded in or lost")


asyncio.run(bounded())
