"""
The one place pptmstr meets ``advtext``: the gate, the fonts, the roles, the views.

Every pane that wants typeset text calls into this package and no other module
in ``pptmstr`` imports ``advtext`` (a test reads the imports). ``advtext`` draws
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
- ``views``: ``ViewCache``, and the calls a pane's typeset branch makes.
- ``diffs``: the diff model of a parked call; which fences are honest diffs.

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

**No pane is in ``ADOPTED``, so every pane draws with its own renderer, the
switches are not offered, and the drawing calls of ``views`` and ``diffs`` raise
if reached.** The gate, the fonts, the settings and the diff model are live.

## What a consumer must not do

- Import ``advtext``. Ask for what is missing here instead.
- Decide for itself whether a body is markdown. ``prose_parts`` is the rule.
- Draw without asking ``enabled(pane)`` in the same frame.
- Share one ``ViewCache`` between two panes.
- Destroy an ImGui context the fonts were loaded in without ``unload()`` first.
"""

from .diffs import accounted, approval_diff, diff, diff_model, is_diff_fence
from .gate import (
    available,
    can_draw,
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
from .views import (
    NotAdopted,
    Stream,
    ViewCache,
    diff_row_height,
    document,
    font,
    label,
    line_height,
    markdown,
    pending,
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
    "NotAdopted",
    "Pane",
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
    "can_draw",
    "checked",
    "chosen",
    "configure",
    "diff",
    "diff_model",
    "diff_row_height",
    "document",
    "enabled",
    "font",
    "is_diff_fence",
    "label",
    "legacy",
    "line_height",
    "load_fonts",
    "markdown",
    "mono",
    "multiline_kind",
    "obtainable",
    "pane_on",
    "panes_from",
    "pending",
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
