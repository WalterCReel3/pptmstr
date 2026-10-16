# Native OS menus: what it would take, and what ordering control ImGui alone gives

**Dated:** 2026-10-15 · **Status:** research note for U6 of `2026-10-15-ui-rework.md`; nothing
here is built · **Asked by:** the operator's "Find a way to plug into the host OS's menu system
and regain some control on ordering" (`ui-notes.md`, quoted in that record)

Versions read: imgui-bundle 1.92.900 as installed in `.venv` (its `hello_imgui.pyi`, its bundled
`libglfw.so.3.4`), hello_imgui's C++ from `pthom/hello_imgui` **master** on GitHub (not the exact
submodule commit 1.92.900 pins; see "Read, run, not run").

## What hello_imgui offers

Nothing native. Its menu bar is an ImGui main menu bar drawn inside the GL window.

- `ImGuiWindowParams.show_menu_bar / show_menu_app / show_menu_view / show_menu_view_themes`
  (`hello_imgui.pyi` ~l.1452-1478) switch its parts on and off.
- `RunnerCallbacks.show_menus` (~l.1784) is ours, drawn inside its `BeginMainMenuBar`.
  `show_app_menu_items` (~l.1794) adds to its App menu.
- `show_view_menu(runner_params)` and `show_app_menu(runner_params)` (~l.3714-3724) draw its
  defaults on demand, from inside our own `show_menus`.
- **Order is fixed by the C++, not configurable.** `ShowMenu` in
  `src/hello_imgui/internal/menu_statusbar.cpp` draws App (if `showMenu_App`), then View (if
  `showMenu_View`), then calls `callbacks.ShowMenus`. So today's bar is **View, Session, Theme,
  Text**: hello_imgui's View comes first because `pptmstr/app.py` `main` sets
  `show_menu_view = True`, and nothing we draw can go before it.
- `BackendPointers` (~l.2492) and `hello_imgui.get_glfw_window_address()` hand over the GLFW
  window. That is the only way out to the OS, and every native route below starts from it.

## Inside ImGui: regaining ordering (recommended, and enough for the operator's ask)

Set `show_menu_view = False` and draw our own "View" from `_menus`. Then every top-level menu is
ours and in the order `_menus` draws them. What hello_imgui's View does
(`internal/docking_details.cpp`, `MenuView_Layouts`, `MenuView_DockableWindows`, `MenuView_Misc`)
and the Python surface to rebuild each part:

| hello_imgui's View item | What it does | Python surface (all in the installed stubs) |
| --- | --- | --- |
| "Restore default layout" | sets `layoutReset` | `runner.docking_params.layout_reset = True` (~l.2444) |
| "Select Layout" submenu | `SwitchLayout(name)`, ticks the current | `hello_imgui.switch_layout`, `current_layout_name` (~l.3640); pptmstr already calls `switch_layout` (`app.py` ~l.1389, ~l.1530) |
| "View All" / "Hide All", one item per window | toggles `isVisible` of closable windows with `includeInViewMenu` | `DockableWindow.is_visible`, `can_be_closed`, `include_in_view_menu` (~l.2294-2316) on `runner.docking_params.dockable_windows` |
| "View Status bar", "FPS" submenu | toggles `showStatusBar`, `showStatus_Fps`, idling | `imgui_window_params.show_status_bar`, `show_status_fps`, `runner.fps_idling` |
| Theme submenu | hello_imgui's own themes | already off (`show_menu_view_themes = False`); pptmstr has its own Theme menu |

What this buys beyond order: our labels (short, per the operator), chord hints on "Select
layout" entries read from the same bindings that act on them (`app.py` ~l.1530 already maps
keys to `TRIAGE`/`FOCUS`), and the name "View" back — `_menus` calls its text menu "Text" only
because hello_imgui owned "View" (the comment above `begin_menu("Text")`).

Cost: we take on the dockable-window list hello_imgui maintained for free. A window added later
through `add_dockable_window` must be listed by whatever reads `dockable_windows` each frame, not
from a list captured at setup. That is a small, testable projection, not a reason to keep theirs.

## Native menus, per OS

None of these is a hello_imgui feature; each is pptmstr talking to the OS behind GLFW's back,
from the GLFW window pointer.

### macOS

- GLFW already creates an `NSMenu` main menu (app menu with Quit, and a Window menu) at init;
  the `GLFW_COCOA_MENUBAR` init hint, default true, controls it (glfw.org intro guide). So there
  is a native bar today, and it is GLFW's, not ours.
- To own it: **pyobjc** (`pyobjc-framework-Cocoa`, not a dependency today) in `post_init`,
  building `NSMenu`/`NSMenuItem`s on `NSApp.mainMenu()` with a Python `NSObject` subclass as the
  target. `glfwGetCocoaWindow` exists in the macOS GLFW but is not needed for the app-wide bar.
- Threading: an item's action runs on the main thread inside GLFW's event poll, which is inside
  hello_imgui's frame loop. It must only enqueue an intent for the next frame (pptmstr's existing
  shape), never touch ImGui. Whether hello_imgui's `run` releases the GIL around the poll, and so
  whether a pyobjc callback can take it, is **not known** — the first thing a probe on a Mac must
  answer.
- Two menus describing one state: the ImGui bar is still drawn unless `show_menu_bar = False`,
  and ticks (theme, typeset, show ended sub-agents) must be mirrored into `NSMenuItem.state`
  each time they change. One projection feeding both renderers is the shape; two hand-written
  menus would drift.

### Linux

- There is no common global menu. GNOME has none. KDE Plasma does: its appmenu module
  (`plasma-workspace/appmenu/appmenu.cpp`) finds an X11 window's menu from the window properties
  `_KDE_NET_WM_APPMENU_SERVICE_NAME` and `_KDE_NET_WM_APPMENU_OBJECT_PATH`, and reads the menu over
  DBus as `com.canonical.dbusmenu`. (Its registrar also accepts `com.canonical.AppMenu.Registrar`
  registrations — recalled, not read on this pass.) XFCE/Budgie need a plugin.
- What pptmstr would need: a DBus library (`jeepney` or `dbus-next`; neither installed), an
  implementation of the `com.canonical.dbusmenu` interface (layout, properties, `Event`,
  `AboutToShow`), and the X11 window id to set the two properties on.
- The window id is reachable: the bundled `libglfw.so.3.4` exports `glfwGetX11Window` (and
  `glfwGetX11Display`), and **exports no Wayland accessor** — the wheel's GLFW is X11-only, so on
  a Wayland session pptmstr runs under XWayland and the X11 route is the only one. (Checked with
  `nm -D`; not called — see below.)
- Payoff is KDE users only, and the ImGui bar must stay for everyone else.

### Windows

- A native menu bar is a Win32 `HMENU` attached with `SetMenu` to the `HWND`
  (`glfwGetWin32Window`), through ctypes. Its `WM_COMMAND` messages go to GLFW's window
  procedure, which ignores them, so pptmstr would have to subclass the window procedure
  (`SetWindowLongPtrW(GWLP_WNDPROC)`) from Python — a ctypes callback on the hottest path of the
  message pump. The menu also takes height from the client area GLFW sized.
- It is still a bar inside the window, as the ImGui one is: the gain is look, not placement.

## Recommendation (the researcher's, not decided)

1. **Do the ImGui-only route now**: `show_menu_view = False`, our own View built from the table
   above, all menus in an order we choose, short labels, chord hints from the bindings. It meets
   "regain some control on ordering" on every platform and needs no new dependency.
2. **Native: macOS only, later, and only as a second renderer of the same menu model.** It is the
   one platform where a native bar is where the user looks, and GLFW has already put one there.
   It needs pyobjc as a darwin-only dependency and the GIL question answered by a probe on a Mac.
3. **Not Linux, not Windows.** Linux reaches KDE users only, for a DBus menu server's worth of
   code; Windows gains a look and costs a window-procedure hook.

Step 2 is cheaper if step 1 builds the menus as data (label, chord, checked, enabled, intent)
drawn by one ImGui function, rather than as `begin_menu` calls with logic inline — that data is
what a native renderer would consume.

## Read, run, not run

- **Read:** `hello_imgui.pyi` in the installed imgui-bundle 1.92.900;
  `imgui_bundle/glfw_utils.py`; `pptmstr/app.py` (`_menus`, `main`); upstream
  `menu_statusbar.cpp` and `docking_details.cpp` from hello_imgui **master** via WebFetch (a
  summarising fetch, not a verbatim read; the order App, View, ShowMenus is consistent with the
  stub's docstring at ~l.1452-1458 and with the bar pptmstr shows); glfw.org's intro guide for
  `GLFW_COCOA_MENUBAR`; KDE `plasma-workspace/appmenu/appmenu.cpp` via WebFetch.
- **Run:** `nm -D` on the bundled `libglfw.so.3.4` (X11 accessors present, no Wayland ones);
  `dir(hello_imgui)` in `.venv` (`get_glfw_window_address`, `get_sdl_window_address`,
  `PlatformBackendType` with `glfw`, `sdl`, `null`).
- **Not run:** a live window. A probe (`hello_imgui.run` with a `post_init` calling
  `glfwGetX11Window` on the GLFW address) failed at `IM_ASSERT(glfwInitSuccess)` because the
  sandbox has no display access, so the X11 window id, the menu order on screen, and every native
  step were never exercised. Nothing was tried on macOS or Windows.
