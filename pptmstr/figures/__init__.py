"""
Figures drawn beside typeset text: everything of them but the drawing.

Pure, and testable without ImGui or a child process: ``sanitise``, the policy every
SVG passes before it is drawn, and ``displaylist``, the bounded list the pane draws.
Not pure: ``mermaid`` runs merman and probes for it in a subprocess, ``render`` owns
the child process, and ``fences`` asks the installed packages what can be drawn.
Nothing here imports ImGui or ``pptmstr.ui``: the figure drawn with ImGui is
``ui.typeset.figure_host``'s, and the faces a child sets labels in are handed to
``render.install`` by whoever composes the window.
"""
