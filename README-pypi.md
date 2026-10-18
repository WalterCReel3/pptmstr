# pptmstr

A multi-agent orchestrator with an immediate-mode UI. It runs several Claude
agent sessions at once, keeps every one of them legible at a glance, and lets
nothing reach your disk that you did not read first: reads are approved
automatically, and everything that writes, runs a shell, reaches the network or
starts a sub-agent waits in a review queue, as a diff where there is one, until
you approve it, reject it with a reason the agent sees, or edit its arguments and
approve the corrected call. Reasoning, cost, context health and the next tool
call are on screen while they happen.

Built on the [Claude Agent SDK](https://pypi.org/project/claude-agent-sdk/) and
[Dear ImGui](https://github.com/pthom/imgui_bundle) (`imgui-bundle`).

## Where it runs

| | CPython |
|---|---|
| Linux x86-64, glibc 2.28 or later | 3.11, 3.12, 3.13, 3.14 |
| macOS 14 or later on Apple Silicon | 3.11, 3.12, 3.13, 3.14 |

On Linux aarch64 it installs without `imtypeset`, the library that sets text,
which has no wheel there; every pane then draws with its own renderer. Not
Windows, and not macOS on Intel, where `imgui-bundle` has no wheel. On Linux with
musl (Alpine) or a glibc older than 2.28, and on macOS older than 14, the install
fails: `imtypeset` has no wheel that fits and no source distribution.

## Install

```
uv tool install pptmstr
```

or

```
pipx install pptmstr
```

Either gives pptmstr an environment of its own, which it needs. It requires three
packages at exact versions that move together:

```
imgui-bundle==1.92.900
imscribe==0.2.0
imtypeset==0.4.3.192900
```

`imtypeset` is a native library linked against that one `imgui-bundle` build and
refused at import beside any other. In a shared environment, anything that wants
another `imgui-bundle`, or a `pip install -U` of one of the three, breaks the
unit, and the failure shows as text falling back rather than at install.

`pptmstr --version` says what was installed and whether the typesetting library
was accepted.

## On Linux

`imgui-bundle`'s GLFW is an X11-only build that loads the X client libraries
when the window opens, so a missing one means no window and no traceback. On
Debian and Ubuntu:

```
sudo apt install libx11-6 libxext6 libxcursor1 libxi6 libxinerama1 libxrandr2 libgl1
```

On a Wayland session it needs XWayland.

The contained modes run each agent's shell commands in the CLI's sandbox, which on
Linux needs `bubblewrap` and `socat`:

```
sudo apt install bubblewrap socat
```

On Ubuntu 24.04 and later the default AppArmor policy stops `bubblewrap` from
creating the user namespaces it needs. If
`sysctl kernel.apparmor_restrict_unprivileged_userns` prints `1`, follow
[Set up Linux and WSL2](https://code.claude.com/docs/en/sandboxing#set-up-linux-and-wsl2)
in the sandboxing documentation. pptmstr does not detect this itself yet.

## Sign-in

pptmstr has no account or sign-in of its own, and reads no credential. It
drives every agent through the Claude Code CLI bundled in the
`claude-agent-sdk` wheel, and every model request goes through that CLI. The
CLI is Claude Code and uses Claude Code's own sign-in: run `claude`, then
`/login`. The wheel puts no `claude` command on `PATH`; that comes with
[Claude Code](https://code.claude.com/docs/en/setup) itself. If
`ANTHROPIC_API_KEY` is set in the environment pptmstr is started from, the CLI
uses that instead.

## More

Source, design records and issues: <https://github.com/WalterCReel3/pptmstr>

MIT licence.
