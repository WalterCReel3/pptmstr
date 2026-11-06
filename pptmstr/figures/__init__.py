"""
Figures drawn beside typeset text.

Pure, and testable without ImGui or a child process: ``sanitise``, the policy every
SVG passes before it is drawn, and ``displaylist``, the bounded list the pane draws.
Not pure: ``mermaid`` runs merman and probes for it in a subprocess, ``render`` owns
the child process, ``fences`` asks the installed packages what can be drawn, and
``host`` draws with ImGui.
"""
