"""
The models the launcher offers.

**A list in code, and nothing asks the API.** pptmstr makes no model request of its own:
every one goes through the bundled Claude Code CLI, which holds the operator's sign-in.
Asking ``GET /v1/models`` for a fresher list would need a credential in pptmstr's hands,
and pptmstr reads none. The cost is the one
``planning/2026-09-25-the-model-list-outlives-the-build-that-verified-it.md`` names: a
model released after this list was edited is not offered until it is edited again.
``--model`` and a pinned pick still reach the session as typed (``launcher.LauncherState``
holds a name, not an index), so a newer model is reachable, only not listed.

**Concrete ids, not aliases**, so that what the combo shows is what the session runs.

**The default is a name, not a position.** ``DEFAULT_MODEL`` is spelled separately from
``MODELS`` so reordering the list cannot move the default -- and its price.
"""

from __future__ import annotations

__all__ = ["DEFAULT_MODEL", "MODELS"]


# Newest-first, the order an operator scanning for the model that just shipped wants.
MODELS: tuple[str, ...] = (
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable-5",
    "claude-haiku-4-5-20251001",
)

# The selection a launch gets when nobody picks one. A name and not ``MODELS[0]``: see
# the module docstring. Same reasoning as ``launcher.LauncherState.policy`` holding a
# ``Policy`` rather than an index.
DEFAULT_MODEL = "claude-sonnet-5"
