# Fleet-empty variants: a mostly-empty FLEET that invites more work

**Dated:** 2026-10-16 · **Status:** a probe for the operator to pick from. Nothing the app
draws has changed · **Answers:** the operator's "An fleet that's mostly empty should _invite_
the user to perform more work."

## What to look at

The images are in `shots/fleet-empty/`. They are gitignored, as the other probes' shots are,
and are rendered at 1464x2880 over the running `--fake` app with the current `theme.py`.

| image | what it is |
|---|---|
| `dark.png`, `light.png` | 4 variants (rows a-d) x 4 states (columns), each cell a FLEET pane under its `fleet` tab, with its controls' exact strings, prefills and click written under it |
| `app-reference-dark.png` | the running `--fake` app, for its real FLEET tab |

The columns are: no sessions; one session; two sessions in one project; and a short fleet
(PPTMSTR with 2 sessions and SCRATCH with 1 closed card). Each pane is 336 x 440, because
TRIAGE docks FLEET at 0.21 of the window and that is 336 px of a 1600 px window. The height
is cut so that the short fleet still leaves most of the pane empty.

To regenerate, run `.venv/bin/python scripts/fleet_empty_probe.py --all`. `--describe` prints
every cell's strings without a display. The tests are in `tests/test_fleet_empty_probe.py`.

**Every variant keeps the pane's `fleet` tab header.** This is spec v2, from the operator:
"We would still want the 'fleet' text as a header though." Above each pane is the dock lane,
and on it the lowercase `fleet` tab, painted by `tabs._paint`, which is the painter the app
runs over ImGui's tab. It is drawn in the `selected` look, with the close cross where
`tabs.close_button` puts it. No variant puts an empty-state title in place of the header.
The lane's geometry (the window-menu arrow, and the tab's offset and width) is the probe's
approximation of ImGui's bar. I compared it by eye against `app-reference-dark.png`, a plain
`--fake` app shot in the same folder, and did not measure it. The cross at the lane's right
end, which the app draws, is not drawn.

**How each pane is drawn.** The headers and cards come from the real rail. `rail.draw` draws
the empty state. For the others, `rail._project_header` and `rail._group` are called in the
order `rail.draw` calls them, so that (d) can draw between groups, and a test pins that the
call sequence and arguments match `rail.draw`'s in all four states. The snapshots are built
by the real `Store` from `AgentSpawned`, `StateChanged`, `AgentFinished` and
`SessionRecalled`. The links are `link_probe.link` at the accent with the underline always
on, which is the set link-variants picked. Clicks on the sheet do nothing.

**What is fixture and what is not.**
- The project directories `/src/pptmstr` and `/src/scratch` do not exist. `tree.session_base`
  places a nonexistent path as itself, so the grouping is still the real one.
- The four resume rows are `SessionRow`s built by hand, standing in for what
  `launcher.scan_sessions` returns. On the sheet their directories are taken as present. The
  tests use the real `launcher.launch_directory` against `tmp_path`.
- The launcher's cwd field is `"."` with no sessions. Otherwise it is the last launch's
  directory, because `left_by_launch` keeps `cwd`.

## The variants

Every string below is exact. `link` is a link, and `dim` is `text_dim` text.

### (a) A quiet line at the foot of the cards

- **Controls:** link `start a session` and dim `· Ctrl+N`. The chord is
  `main_menu.chord_label(launcher.CHORD)`, so it is whatever the handler is bound to.
- **Prefills:** nothing.
- **Click:** `launcher.request_open()`. New Task opens as it was left and launches in the cwd
  field, resolved.
- **For:** it is the cheapest of the four, the same in every state, and it never moves
  anything.
- **Against:** it says nothing that the Session menu and Ctrl+N do not already say. Its
  value is discoverability, nothing more.

### (b) The empty space as a drop-target panel

- **Controls:** a dashed `text_dim` frame that fills what the pane has left. Inside it: link
  `New Task` (which is `launcher.TITLE`) and dim `Ctrl+N`, then a 12.5 px dim line
  `in <cwd field, resolved>`, clipped with an ellipsis.
- **Visibility:** the panel is not drawn when less than 72 px is left.
- **Prefills:** nothing.
- **Click:** the same as (a).
- **For:** it is the only variant that shows where a fresh launch will run before the modal
  opens.
- **Against:**
  - "Drop target" promises a drag that does not exist. Nothing can be dropped on the pane.
  - Its height is the pane's leftover space, so it moves at the model's pace. A card going
    from active to blocked grows a line, and the panel shrinks by that line. The rail's own
    rule (`rail.py`, `_group`) accepts card motion because it is the existing behaviour. This
    would be new motion, on the one element that has no reason to move.
  - On a full fleet it disappears, so the invitation is missing exactly when it would be
    least noise.

### (c) Recent and bookmarked sessions offered for resume

- **Controls:** up to 3 lines, each made of a dim `*` (bookmarked) or `·`, then a link with
  the row's `label`, then dim `launcher.age_label` (for example `2d ago`). The link is the
  part that gives way: it is clipped with an ellipsis so that the age stays. After them comes
  (a)'s line.
- **Which rows:** rows already on the fleet as a card are left out (`s-old` in the short
  fleet). The rest are ordered bookmarked first, then newest, which is the order
  `scan_sessions` hands the picker.
- **Prefills:** `picked = <session id>`, `directory = launch_directory(row)`, and model, team
  and cap from the session's record.
- **Click:** `request_open()`, then `LauncherState.pick_session(row)`. Launch then resumes the
  session.
- **Gone directory:** a row whose directory is gone is drawn with its link disabled and the
  dim text `directory gone`, because Launch would be held anyway (tested).
- **For:** when the fleet is empty, this is the most useful thing the pane could say.
- **Against:**
  - It needs a session scan outside the launcher. Today the scan runs only on
    `begin_open`, on a worker, and every open rescans because "a listing from an hour ago is
    not a listing of what is there". The fleet would need its own scan, its own refresh rule
    and its own staleness.
  - The scan is scoped to one directory (the cwd field), so the rows depend on where the
    operator last launched.
  - The persisted fleet already recalls sessions as closed cards. Two lists of past work on
    one pane, one as cards and one as links, is the duplication the exclusion only partly
    hides.

### (d) `+ new in <project>`, under each project's group

- **Controls:** one link `+ new in <project>` after each project's cards. `<project>` is the
  `projects.group_roots` key, and the UNFILED group gets none. With no sessions there are no
  groups, so it is one link `+ new in <basename of the cwd field>` with dim `· Ctrl+N`.
- **Prefills:** `cwd = session_base` of the project's newest root.
- **Click:** `request_open()` with that cwd. New Task opens on a blank task in that
  directory, with model, team and brief as they were left.
- **For:**
  - It uses only facts the rail already holds: no scan and no I/O.
  - It answers the most common next step, "more work in this project".
  - It is in stable spatial order with the groups and moves only when a group does.
- **Against:**
  - It costs one line per project, at any fleet size.
  - It writes the cwd field. The draft survives a cancel by design (`LauncherState`), so
    one click silently moves a half-typed draft to another directory. It should set the cwd
    only when the draft is untouched (compare against `left_by_launch`), or say on the modal
    that it moved the field.
  - A project is a directory's *name*, so two directories with the same name share a group.
    The newest root's directory is the one taken, and the other is unreachable from the
    link.

## Recommendation (the builder's)

Build **(d) together with (a)**:
- `+ new in <project>` under each group, setting the cwd only when the draft is untouched.
- One `start a session · Ctrl+N` line at the foot of the cards.
- (a)'s line replaces the rail's empty-state second line.

This covers both "more here" and "something new". It needs no new data source, and it adds
no motion the rail does not already have.

Not (b): its affordance is false, and its size tracks card density. Not (c), for now: a
fleet-side session scan is a seam of its own, and the persisted fleet's closed cards already
offer the most recent work. If (c) is wanted later, it belongs in the empty state only, where
it duplicates nothing.

## Seen while drawing this, not asked

- The rail's empty text says `start one from the launcher below.`, but the launcher became
  a modal (`planning/2026-08-10-launcher-as-a-modal.md`) and nothing is below. Every variant
  is drawn under that line as it stands. Any of them should replace it.
- `rail.header_counts` says `1 sessions` (visible in every one-session cell).
- `projects.project_key` raises `ValueError` on a cwd that holds a NUL, through
  `tree._resolved` → `realpath`, and `store._VERBATIM` keeps `cwd` as it arrived. A session
  file whose `cwd` decodes to a NUL would raise inside `rail.draw`. I hit this in the test,
  but I did not check whether `sessionfile` refuses such a file first.
