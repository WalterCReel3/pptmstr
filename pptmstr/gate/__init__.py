"""
The approval gate, by what each part requires.

- ``policy``: the rungs (``Policy``, ``Disposition``) and what each asks of a
  session: ``requires_containment``, ``inherits_to_subagents``.
- ``tools``: the tool names the gate knows and the tables that sort them --
  ``AUTO``, ``REVIEW``, ``BUS_AUTO``, ``AUTONOMOUS_AUTO``, ``FILE_READS``,
  ``TREE_READS`` -- with ``peer_channel`` and ``states_provenance``.
- ``diff``: the before/after texts of a parked edit (``DiffText``) and the
  unified diff drawn from them (``render_texts``, ``diff_line_kind``).

Each imports nothing from pptmstr, which is what lets ``model`` import them.
Classification, the queue row, the records reach and the read of an edit's
target are in ``pptmstr.approval``. This ``__init__`` imports and re-exports
nothing: a name is imported from the module that defines it.
"""
