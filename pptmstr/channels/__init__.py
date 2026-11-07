"""
The links between leads: one package for a domain whose pieces need different
things, one module per requirement.

- ``records``: the channel's own values -- a ``Channel``, its id and the rule for
  its name, a charter mark, and the refusals and marks the peer tools and the
  thread speak in. Imports nothing from pptmstr but ``ids``, so ``model`` can
  import it.
- ``log``: a link's facts on disk, one log per link, and the serial worker that
  appends them. Only ``app`` imports it.

What stays elsewhere, and why, is in
``planning/2026-11-07-channels-and-ids-have-one-owner-each.md``: the channel
intents, effects and obligations with their unions, ``_apply``'s channel arms in
``_apply``, ``Concern``'s peer fields on ``Concern``, the projections on
``Snapshot`` and the panes under ``ui``.

Nothing is imported or re-exported here: importing the package must not reach
the disk through ``log``.
"""
