"""
The board's read side: one session's tasks and concerns as the operator and its
agents both read them, one module per requirement.

- ``words``: what a text's standing, a stated property, a not-run list and an
  incident mark read as -- ``basis_label``, ``property_label``, ``not_run_label``,
  ``incident_marker``. Each takes a value, not a snapshot, so it imports nothing
  from pptmstr at runtime.
- ``rows``: the task and concern rows -- ``board_tasks``, ``board_concerns``,
  ``unrun_ledger``, ``incidents`` -- and the names a board gives its participants
  (``role_name``). Reads a ``Snapshot``.
- ``graph``: the dependency graph's questions over the rows -- ``why_blocked``,
  ``ready``, ``open_tasks``, ``dry_run_declare``.
- ``digest``: one round as a digest over the rows and words -- ``digest``,
  ``digest_mark``, ``owed_counts``.

The board's persistence half is not here: ``boardfacts``, ``boardsync`` and
``boardlog`` are the board's instance of the journal, and the panes that draw these
rows are under ``ui``. Why, is in ``planning/2026-11-07-board-and-gate-packages.md``.

Nothing is imported or re-exported here, so importing ``words`` runs no import
from pptmstr.
"""
