"""
The one place pptmstr meets ``imscribe``: the gate, the fonts, the roles, the views.

Every pane that wants typeset text calls into this package and no other module
in ``pptmstr`` imports ``imscribe`` (a test reads the imports). ``imscribe`` draws
through imtypeset, a native library beside the ``imgui-bundle`` wheel; what it
needs of a host is small but easy to get five different ways in five panes, and
those answers live here once.

- ``roles``: pptmstr's own policy. The role catalogue, the two family slots,
  sizes and scales, the pane switches, the settings read into them, and
  ``enabled(pane)``. No library import.
- ``parts``: ``Kind``, ``Part``, and ``prose_parts``, the one rule for whether a
  body is markdown. Pure.
- ``gate``: whether the library can draw and the sentence when it cannot; its
  fonts; ``unload``.
- ``themes``: a role's face, size and leading as the library's theme, pptmstr's
  inks as its palette, and which text the library sets at all.
- ``views``: ``ViewCache``, and the calls a pane's typeset branch makes.
- ``diffs``: the diff model of a parked call, a view of it, which fences are
  honest diffs, and who draws a diff (``diff_adopted``, and of one diff
  ``diffs.unfit``).

## A pane asks first

``enabled(pane)`` false means that pane uses its own renderer, untouched. It is
false when the operator switched typesetting or that pane off, when the pane's
typeset branch is not built on the library as it is now (``roles.ADOPTED``), and
when the library cannot draw (``can_draw``): not installed, refused, its fonts
not loaded, or stopped while drawing.

``available()`` is the operator's question and not a pane's: whether typeset
text can be had at all, which is the library drawing and at least one pane
adopted. The Text menu and the settings dialog offer their switches by it, and
say ``unavailable_reason()``, one sentence, when it is false.

**Every pane is in ``ADOPTED``, and the settings dialog draws its samples
through the same calls.** CONTEXT's typeset branch is one of four modes of the
log and not the one it starts in (``transcript_pane.DEFAULT_MODE``).

**The library sets markdown, and ImGui draws text that is not markdown**
(``themes.native``): monospace set by the library does not keep its columns, by
pptmstr's measurement. So a literal part, ``plain()`` and a label are ImGui
items in the chrome's font or the library's own atlas entry.

**A diff is the library's where the library that is running keeps a column,
and its pane's own renderer where it does not** (``diff_adopted``): the
question is measured of the running library (``gate.columns``), with the diff
view on a whole-pixel cell (``gate.DIFF_CELL``), and is true on the one
pptmstr is built against at every size a diff is set at. So DETAIL and the
inbox row draw an approval's diff with the library's diff view and a settled
document hosts an honest fenced diff in one, each diff being asked about as
well (``diffs.unfit``). ``diffs`` has the numbers, what the cell costs in
glyph size, the rules an adopted view is held to, and the one constant that
overrides the measurement (``diffs.ADOPT``).

## What a consumer must not do

- Import ``imscribe``. Ask for what is missing here instead.
- Decide for itself whether a body is markdown. ``prose_parts`` is the rule.
- Draw without asking ``enabled(pane)`` in the same frame.
- Share one ``ViewCache`` between two panes.
- Destroy an ImGui context the fonts were loaded in without ``unload()`` first.
"""

from .diffs import Parked as ParkedDiff
from .diffs import (
    accounted,
    approval_diff,
    diff,
    diff_model,
    diff_row_height,
    is_diff_fence,
)
from .diffs import adopted as diff_adopted
from .diffs import draws as diff_draws
from .diffs import renderer as diff_renderer
from .gate import (
    available,
    can_draw,
    face_font,
    load_fonts,
    obtainable,
    pre_frame,
    unavailable_reason,
    unload,
)
from .parts import _MULTILINE, Kind, Part, multiline_kind, prose_parts
from .roles import (
    _LEGACY,
    _STATE,
    ADOPTED,
    BODY_PX,
    DEFAULT_PRESET,
    DEFAULT_PROPORTIONAL,
    DEFAULTS,
    LEADING,
    PANE_LABELS,
    PROPORTIONALS,
    ROLE_LABELS,
    SCALES,
    SLOTS,
    STEPS,
    Pane,
    Proportional,
    Role,
    RoleStyle,
    Slot,
    Weight,
    body_px,
    checked,
    chosen,
    configure,
    enabled,
    legacy,
    mono,
    pane_on,
    panes_from,
    pitch,
    proportional,
    proportionals,
    px,
    roles_from,
    scale,
    style,
    wanted,
)
from .themes import build_palette, palette
from .views import (
    Stream,
    ViewCache,
    document,
    font,
    imfont,
    label,
    line_height,
    markdown,
    plain,
    stream,
)

__all__ = [
    "ADOPTED",
    "BODY_PX",
    "DEFAULTS",
    "DEFAULT_PRESET",
    "DEFAULT_PROPORTIONAL",
    "LEADING",
    "PANE_LABELS",
    "PROPORTIONALS",
    "ROLE_LABELS",
    "SCALES",
    "SLOTS",
    "STEPS",
    "Kind",
    "Pane",
    "ParkedDiff",
    "Part",
    "Proportional",
    "Role",
    "RoleStyle",
    "Slot",
    "Stream",
    "ViewCache",
    "Weight",
    "_LEGACY",
    "_MULTILINE",
    "_STATE",
    "accounted",
    "approval_diff",
    "available",
    "body_px",
    "build_palette",
    "can_draw",
    "checked",
    "chosen",
    "configure",
    "diff",
    "diff_adopted",
    "diff_draws",
    "diff_model",
    "diff_renderer",
    "diff_row_height",
    "document",
    "enabled",
    "face_font",
    "font",
    "imfont",
    "is_diff_fence",
    "label",
    "legacy",
    "line_height",
    "load_fonts",
    "markdown",
    "mono",
    "multiline_kind",
    "obtainable",
    "palette",
    "pane_on",
    "panes_from",
    "pitch",
    "plain",
    "pre_frame",
    "prose_parts",
    "proportional",
    "proportionals",
    "px",
    "roles_from",
    "scale",
    "stream",
    "style",
    "unavailable_reason",
    "unload",
    "wanted",
]
