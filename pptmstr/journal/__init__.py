"""
A journal: facts appended one record per file to a directory, and read back as the
ordered prefix that is whole.

The board log (``boardlog``) and a channel's log (``channels.log``) are both journals.
What they share is here, in two modules split by what they require:

- ``codec``: pure. One record as text and back, under a kind table the caller
  passes (``Kinds``), the writer's stop marker every table holds
  (``RecordingStopped``), a record of a kind the table lacks (``Unrecognised``), and
  what a read yields (``Log``, ``Damage``).
- ``files``: the disk. Where a session's journal lives (``session_dir``), the
  linked append, the strict ordered read, and the listing.

Nothing is re-exported here: a caller names the module it uses, so a test that
patches ``files.append`` patches what the caller looks up.
"""
