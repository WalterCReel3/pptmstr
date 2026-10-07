# What happens when one CLI message exceeds the buffer ceiling

Measured 2026-09-24 against `claude-agent-sdk` 0.2.134 (the copy in `.venv/`),
CPython 3.11, by `scripts/verify_overrun_recovery.py`.

The recovery work this was gathered for was descoped mid-investigation: the
decision was to raise the ceiling and ship, and to not build overrun recovery in
that session. What follows is kept only so the next session does not re-derive
it. **The recommendation between "transport-level skip" and "session resume" was
never reached and nothing here should be read as one.**

---

## How this was measured, and the one thing that limits it

The probe drives the **real** `SubprocessCLITransport` and the **real** `Query`
against a **fake CLI** — a small Python script emitting NDJSON on stdout — rather
than against the real `claude` binary.

That choice buys an exact payload size and, more importantly, a message emitted
*after* the oversized one, which is the only way to distinguish "the stream
recovered" from "the stream ended". A real CLI cannot be made to do that on
demand.

**The limit that follows, and it is a real one:** this is a synthetic
reproduction. Every result below is a property of the SDK's framing and error
plumbing. **The operator's actual incident was never reproduced against the real
CLI**, so nothing here establishes which message type carried the oversized
payload in that incident, or that the real CLI fails identically.

Payload: a single 3 MiB JSON line (no embedded newline), sequenced between four
small messages marked `before`, `oversized`, `after`, `terminal`.

---

## Measured

### The overrun ends the stream. It does not merely drop one message.

At the stock 1 MiB ceiling, iterating `transport.read_messages()` directly:

| | observed |
|---|---|
| markers delivered | `['before']` |
| exception | `CLIJSONDecodeError` (`claude_agent_sdk._errors`) |
| text | `Failed to decode JSON: JSON message exceeded maximum buffer size of 1048576 bytes` |
| re-entering the **same** generator | yields `[]`, **raises nothing** |

`after` and `terminal` were never delivered. The generator is exhausted, not
merely interrupted — re-entering it is silent rather than raising again. So the
cost of an overrun is the **remainder of the session**, not the one oversized
message.

### The exception type is erased before it reaches us. (Confirms the reviewer's prediction.)

Reading the same 3 MiB line through the real `Query`, as a consumer does:

| | observed |
|---|---|
| markers delivered | `['before']` |
| exception type | `Exception` — **`builtins`**, not the SDK's class |
| `type(exc).__name__ == 'SDKJSONDecodeError'` | **`False`** |
| text | unchanged from above |
| re-entering `receive_messages()` | yields `[]` |

The prediction was right. The path, read from source and consistent with the
measurement:

- `_internal/query.py` `_read_messages` catches the error in a blanket
  `except Exception as e` and **does not re-raise**. It stringifies it
  (`error_text = str(e)`) and sends `{"type": "error", "error": error_text}` on
  the internal channel.
- Its `finally` then sends `{"type": "end"}` and **closes** the channel — which
  is why the stream is over regardless of what a consumer does.
- `_internal/query.py` `receive_messages` turns that dict back into
  `raise Exception(message.get("error", "Unknown error"))` — a **bare
  `Exception`**.

**Consequence for any future fix:** an `except SDKJSONDecodeError` at our
`async for` in `driver.py` would never fire. The class does not survive the trip.
Only the message text does, and matching on it is matching on a string the SDK is
free to reword.

Note that the transport *does* raise the SDK's own class — case A above observes
`CLIJSONDecodeError`. The erasure happens in `Query`, one layer above it. So both
statements are true and they are about different layers: the transport raises
`CLIJSONDecodeError`, and a consumer of `Query`/`ClaudeSDKClient` — which is what
`driver.py` is — sees `Exception`.

### The naive catch produces a session that looks like it finished cleanly

This is the trap, and it is worth stating on its own because the measurement
looks reassuring. Re-entering the stream after catching the error does **not**
raise again and does **not** hang: measured as `[]` with no exception, in both
case A and case B.

The reason is that `_read_messages`'s `finally` has already queued
`{"type": "end"}` and closed the send side, so a retry iterates once, sees the
`end` sentinel, and breaks normally. A `try`/`except` around our `async for` that
then continues would therefore observe an orderly end of stream and report the
node DONE — having silently lost the oversized message and every message after
it. That is strictly worse than the current terminal failure, which at least
writes an error the operator can see.

So "we tried catching it and it seemed fine" is a conclusion this code will
actively produce. It is wrong.

### `SDKJSONDecodeError` is an alias, and is not public

`subprocess_cli.py:22` reads:

```python
from ..._errors import CLIJSONDecodeError as SDKJSONDecodeError
```

There is no `SDKJSONDecodeError` in `_errors.py`, and the name is not exported
from the `claude_agent_sdk` package — `CLIJSONDecodeError` is. They are the same
object, so the alias is harmless, but code importing `SDKJSONDecodeError` must
reach into `_internal` to get it. Worth knowing before anyone writes that import.

### Raising the ceiling restores the whole stream

Identical run, ceiling set to 6 MiB (above the 3 MiB payload):

| | observed |
|---|---|
| markers delivered | `['before', 'oversized', 'after', 'terminal']` |
| exception | none |

The oversized message itself is delivered intact, and so is everything after it.
This is the control that establishes the buffer guard is the sole cause here, and
not some other defect in the fake CLI or the framing.

---

## Not determined

- **The real incident was not reproduced.** No run against the real `claude`
  binary, no real oversized `tool_result`. Which message type carried the
  operator's oversized payload is still unknown, and that is the fact that would
  decide what a resume would have to replay.
- **Whether a resumed session replays the same oversized message and dies again.**
  Not measured, not reasoned about to a conclusion. Entirely open.
- **Whether a transport-level skip is viable.** The subclassing question, the cost
  of pinning to the private `_read_messages_impl`, and whether dropping a message
  desyncs the translator were all left unexamined.
- **Which of the two guards fired.** `_read_messages_impl` bounds both the
  completed line (`len(line)`) and the in-flight partial (`framer.pending_len`).
  The inner `ValueError`'s reported length distinguishes them, and it was not
  captured. This matters only to a skip design: if the guard trips on
  `pending_len`, the framer is holding a *partial* line at that moment, so
  "dropping the message" means consuming and discarding chunks until the next
  newline — a state machine, not a `try`/`except`.

---

## Reproducing

```
.venv/bin/python scripts/verify_overrun_recovery.py
```

Print-only. No API tokens, no app state. The `RuntimeError: Event loop is closed`
lines at exit are the probe's own subprocess teardown during interpreter
shutdown, not part of the measurement.
