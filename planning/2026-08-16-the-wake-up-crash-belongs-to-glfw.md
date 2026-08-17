# The wake-up crash belongs to GLFW

**Recorded 2026-08-16**, after pptmstr died on a laptop wake-up. Nothing here is
built and nothing should be. This is an upstream defect with no upstream fix, parked
so it can be chased later as a PR against another repository.

---

## The crash

```
X Error of failed request:  BadRRCrtc (invalid Crtc parameter)
  Major opcode of failed request:  139 (RANDR)
  Minor opcode of failed request:  20 (RRGetCrtcInfo)
  Crtc id in failed request: 0x440
  Serial number of failed request:  12235108
```

No Python traceback, because there was never a Python exception. Xlib's
`_XDefaultError` prints this block and calls `exit(1)` from inside the C stack.

## The chain

1. hello_imgui's `Impl_NewFrame_PlatformBackend()` calls `ImGui_ImplGlfw_NewFrame()`
   once per frame. In imgui `v1.92.8-docking` — the version vendored in
   imgui-bundle 1.92.801 — that function calls `ImGui_ImplGlfw_UpdateMonitors()`
   unconditionally. There is no viewports guard, and viewports are off here.
2. `UpdateMonitors` calls `glfwGetMonitorPos` and `glfwGetMonitorWorkarea` per
   monitor per frame.
3. On X11 both resolve to `XRRGetCrtcInfo(display, sr, monitor->x11.crtc)` — screen
   resources fetched fresh, CRTC id read from cache.
4. `_glfwPollMonitorsX11` matches a re-seen monitor on `x11.output` and `continue`s
   past the assignment that would refresh `x11.crtc`. A monitor GLFW already knows
   about keeps its cached CRTC id indefinitely.
5. Across a wake the id names a CRTC the server no longer has. The next frame kills
   the process.

## Why this machine

The session is KDE Plasma on Wayland; `DISPLAY=:1` is Xwayland. Xwayland creates and
destroys a RandR CRTC with each Wayland output, so a lid or DPMS cycle genuinely
recycles the XID. Under a real Xorg server, CRTC XIDs are allocated once at server
start and the defect stays dormant — which is why this is a laptop-wake symptom and
not a permanent one.

`Serial: 12235108` discriminates nothing. Machine uptime was 50.8 days; the serial
records a long-lived connection, not a request rate.

## Why we are not fixing it here

pptmstr contains no GLFW call of any kind — no `import glfw`, no hello_imgui monitor
API. The only viewport touch in the tree is `imgui.get_main_viewport()` at
`ui/launcher.py:141`, which reads ImGui state and issues no X request. Our only
influence on the failing path is its *rate*: `app.py:740` sets `fps_idle` (9.0 by
default, `settings.py:45`), and `app.py:760` switches idling on `snap.any_active`.
That is exposure, not cause.

**The obvious mitigation is a trap.** Installing a non-fatal X error handler so Xlib
stops calling `exit(1)` makes things worse. `_glfwGetMonitorWorkareaX11` is:

```c
XRRCrtcInfo* ci = XRRGetCrtcInfo(_glfw.x11.display, sr, monitor->x11.crtc);

areaX = ci->x;
```

with no NULL check, in glfw 3.4 and on master today. `XRRGetCrtcInfo` returns NULL
when the reply is an error and a handler returns, so a benign handler trades a clean,
diagnosable `exit(1)` for a silent SIGSEGV. Its sibling `_glfwGetMonitorPosX11` *does*
check `if (ci)`, so which of the two per-frame call sites you land on is a race.

There is no released fix to upgrade into: glfw#1147 added `if (!ci) continue;` to the
polling loop only.

## What a PR would have to say

Two separate defects, either of which alone would have prevented this:

- **`x11_monitor.c`** — refresh `monitor->x11.crtc` on the retained-monitor path in
  `_glfwPollMonitorsX11`, and NULL-check `ci` in `_glfwGetMonitorWorkareaX11` as the
  neighbouring functions already do.
- **`imgui_impl_glfw.cpp`** — `ImGui_ImplGlfw_UpdateMonitors()` running every frame
  with viewports disabled is pure cost even when it is not fatal.

Cross-toolkit confirmation that this is GLFW-level and not ours: fyne-io/fyne#5899 is
the identical error and identical minor opcode on display idle→wake from Go/go-gl's
vendored GLFW, and kitty#700 reports the same on suspend. RANDR's major opcode is
assigned per connection, so the `139` here and the `140` in fyne's report are the same
call.

**Unproven, and it does not change the conclusion:** nobody established which of the
two call sites issued the fatal request, or whether it was the stale cache rather than
a race against a queued `RRNotify`. Both have the same root and the same fix surface.
`gdb -p <pid> -batch -ex 'b XRRGetCrtcInfo' -ex c -ex bt` on a running instance would
settle it without a suspend cycle, since the caller is invariant and only the
staleness of its argument varies.

## The part that is ours, and is not this

Because the death is `exit(1)` from C, the `finally:` at `app.py:796-803` never runs:
`pool.shutdown()` and `bridge.stop()` are both skipped. The Claude CLI children do
*not* leak — checked after this crash, no `claude` process was reparented to init, so
stdin EOF collects them. What is lost is parked approvals and in-flight session state.

That is a durability question about any hard kill, not an X11 question, and it wants
its own record rather than a line in this one.
