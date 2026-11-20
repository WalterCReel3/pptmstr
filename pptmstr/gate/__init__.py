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

- ``rows``: the queue row (``compose``, ``Summary``, ``command_shown``) and what
  DETAIL says of a declaration's provenance. Pure; it imports ``model``, and the
  store composes a row with it.
- ``reach``: where a call's paths lead against pptmstr's records
  (``records_read``, ``records_written``). Resolves paths on the disk.
- ``classify``: whether a call runs unattended on a rung (``classify``), through
  ``reach``.
- ``park``: what the driver computes once when a call parks -- the row with its
  records mark (``summary_of``, ``summarize``) and the texts of an edit, read
  from the file it targets (``diff_texts``, ``render_diff``).
- ``sandbox``: the containment configuration, as a value
  (``containment_settings``).

This ``__init__`` imports and re-exports nothing: a name is imported from the
module that defines it.
"""
